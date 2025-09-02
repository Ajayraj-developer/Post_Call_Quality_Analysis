import librosa
import numpy as np
import os
import pickle
from tensorflow.keras.models import model_from_json, Sequential

def calculate_average_speech_rate(audio_path, segment_window=2.5, segment_step=1.0, sr=22050):
    """
    Calculates the average speech rate (average ZCR) across the whole audio duration,
    by dividing the audio into overlapping segments and averaging the ZCRs.
    Returns:
        avg_speech_rate: float (average ZCR across all segments)
        segment_times: list of segment midpoints (for plotting)
        segment_zcrs: list of average ZCR per segment (for plotting)
    """
    y, sr = librosa.load(audio_path, sr=sr)
    duration = librosa.get_duration(y=y, sr=sr)
    segment_times = []
    segment_zcrs = []

    start = 0.0
    while start + segment_window <= duration:
        segment = y[int(start*sr):int((start+segment_window)*sr)]
        # Calculate ZCR for this segment
        segment_zcr = librosa.feature.zero_crossing_rate(segment, frame_length=2048, hop_length=512)[0]
        avg_zcr = np.mean(segment_zcr)
        segment_zcrs.append(avg_zcr)
        segment_times.append(start + segment_window/2)
        start += segment_step

    if segment_zcrs:
        avg_speech_rate = float(np.mean(segment_zcrs))
    else:
        avg_speech_rate = 0.0

    return avg_speech_rate, segment_times, segment_zcrs

def calculate_energy_over_time(audio_path, sr=22050, frame_length=2048, hop_length=512):
    """
    Calculates RMS energy and corresponding times for the audio.
    Returns:
        energy_times: list of time stamps (seconds)
        energy_values: list of RMS values
    """
    y, sr = librosa.load(audio_path, sr=sr)
    rms = librosa.feature.rms(y=y, frame_length=frame_length, hop_length=hop_length)[0]
    energy_times = librosa.frames_to_time(range(len(rms)), sr=sr, hop_length=hop_length, n_fft=frame_length)
    return energy_times.tolist(), rms.tolist()

def get_calm_score(audio_path):
    # Use absolute paths for model and encoders (now using Tone_analysis directory, updated for workspace)
    model_dir = os.path.join(os.path.dirname(__file__), 'model')
    if not hasattr(get_calm_score, 'model'):
        with open(os.path.join(model_dir, 'CNN_model.json'), 'r') as json_file:
            loaded_model_json = json_file.read()
        model = model_from_json(loaded_model_json, custom_objects={'Sequential': Sequential})
        model.load_weights(os.path.join(model_dir, 'best_model1_weights.h5'))
        with open(os.path.join(model_dir, 'scaler2.pickle'), 'rb') as f:
            scaler2 = pickle.load(f)
        with open(os.path.join(model_dir, 'encoder2.pickle'), 'rb') as f:
            encoder2 = pickle.load(f)
        get_calm_score.model = model
        get_calm_score.scaler2 = scaler2
        get_calm_score.encoder2 = encoder2
    else:
        model = get_calm_score.model
        scaler2 = get_calm_score.scaler2
        encoder2 = get_calm_score.encoder2

    def zcr(data, frame_length, hop_length):
        zcr = librosa.feature.zero_crossing_rate(data, frame_length=frame_length, hop_length=hop_length)
        return np.squeeze(zcr)

    def rmse(data, frame_length=2048, hop_length=512):
        rmse = librosa.feature.rms(y=data, frame_length=frame_length, hop_length=hop_length)
        return np.squeeze(rmse)

    def mfcc(data, sr, frame_length=2048, hop_length=512, flatten: bool = True):
        mfcc_feat = librosa.feature.mfcc(y=data, sr=sr, n_fft=frame_length, hop_length=hop_length)
        return np.squeeze(mfcc_feat.T) if not flatten else np.ravel(mfcc_feat.T)

    def extract_features(data, sr=22050, frame_length=2048, hop_length=512):
        result = np.array([])
        result = np.hstack((result,
                            zcr(data, frame_length, hop_length),
                            rmse(data, frame_length, hop_length),
                            mfcc(data, sr, frame_length, hop_length)
                           ))
        return result

    y, sr = librosa.load(audio_path, sr=22050)
    duration = librosa.get_duration(y=y, sr=sr)
    window = 2.5
    step = 1.0
    start = 0.0
    polarities = []
    expected_feat_len = 2376
    categories = encoder2.categories_[0]
    def decode_label(label):
        return label.decode() if isinstance(label, bytes) else str(label)
    # Gradioapp.py mapping
    emotion_polarity = {
        'happy': 'neutral',
        'calm': 'calm',
        'surprise': 'neutral',
        'neutral': 'calm',
        'sad': 'calm',
        'angry': 'angry',
        'fear': 'calm',
        'disgust': 'neutral'
    }
    polarity_numeric = {'calm': -1, 'neutral': 0, 'angry': 1}
    angry_threshold = 0.9
    while start + window <= duration:
        segment = y[int(start*sr):int((start+window)*sr)]
        features = extract_features(segment, sr=sr, frame_length=2048, hop_length=512)
        if features.shape[0] != expected_feat_len:
            start += step
            continue
        features = np.reshape(features, (1, -1))
        features = scaler2.transform(features)
        features = np.expand_dims(features, axis=2)
        pred_probs = model.predict(features)
        pred_label = encoder2.inverse_transform(pred_probs)[0][0]
        # Angry threshold logic as in gradioapp.py
        angry_idx = np.where(categories == b"angry")[0] if isinstance(categories[0], bytes) else np.where(categories == "angry")[0]
        angry_prob = pred_probs[0, angry_idx[0]] if len(angry_idx) > 0 else 0.0
        if pred_label == "angry" and angry_prob < angry_threshold:
            label = "neutral"
        else:
            label = pred_label
        mapped_polarity = emotion_polarity.get(label, 'neutral')
        polarity = polarity_numeric.get(mapped_polarity, 0)
        polarities.append(polarity)
        start += step
    # --- Calm score (custom scoring, as in gradioapp.py) ---
    score = 0
    for p in polarities:
        if p == -1:      # calm
            score += 2
        elif p == 0:    # neutral
            score += 1.8
        # angry (p == 1): add 0
    total_possible = len(polarities) * 2 if polarities else 1
    calm_score = 10 * score / total_possible
    calm_score = max(0.0, min(10.0, calm_score))
    return calm_score

def get_vad_over_time(audio_path):
    # Use absolute paths for model and encoders (now using Tone_analysis directory, updated for workspace)
    model_dir = os.path.join(os.path.dirname(__file__), 'model')
    if not hasattr(get_vad_over_time, 'model'):
        with open(os.path.join(model_dir, 'CNN_model.json'), 'r') as json_file:
            loaded_model_json = json_file.read()
        model = model_from_json(loaded_model_json, custom_objects={'Sequential': Sequential})
        model.load_weights(os.path.join(model_dir, 'best_model1_weights.h5'))
        with open(os.path.join(model_dir, 'scaler2.pickle'), 'rb') as f:
            scaler2 = pickle.load(f)
        with open(os.path.join(model_dir, 'encoder2.pickle'), 'rb') as f:
            encoder2 = pickle.load(f)
        get_vad_over_time.model = model
        get_vad_over_time.scaler2 = scaler2
        get_vad_over_time.encoder2 = encoder2
    else:
        model = get_vad_over_time.model
        scaler2 = get_vad_over_time.scaler2
        encoder2 = get_vad_over_time.encoder2

    def zcr(data, frame_length, hop_length):
        zcr = librosa.feature.zero_crossing_rate(data, frame_length=frame_length, hop_length=hop_length)
        return np.squeeze(zcr)

    def rmse(data, frame_length=2048, hop_length=512):
        rmse = librosa.feature.rms(y=data, frame_length=frame_length, hop_length=hop_length)
        return np.squeeze(rmse)

    def mfcc(data, sr, frame_length=2048, hop_length=512, flatten: bool = True):
        mfcc_feat = librosa.feature.mfcc(y=data, sr=sr, n_fft=frame_length, hop_length=hop_length)
        return np.squeeze(mfcc_feat.T) if not flatten else np.ravel(mfcc_feat.T)

    def extract_features(data, sr=22050, frame_length=2048, hop_length=512):
        result = np.array([])
        result = np.hstack((result,
                            zcr(data, frame_length, hop_length),
                            rmse(data, frame_length, hop_length),
                            mfcc(data, sr, frame_length, hop_length)
                           ))
        return result

    y, sr = librosa.load(audio_path, sr=22050)
    duration = librosa.get_duration(y=y, sr=sr)
    window = 2.5
    step = 1.0
    start = 0.0
    vad_times = []
    valence_list = []
    arousal_list = []
    dominance_list = []
    expected_feat_len = 2376
    while start + window <= duration:
        segment = y[int(start*sr):int((start+window)*sr)]
        features = extract_features(segment, sr=sr, frame_length=2048, hop_length=512)
        if features.shape[0] != expected_feat_len:
            start += step
            continue
        features = np.reshape(features, (1, -1))
        features = scaler2.transform(features)
        features = np.expand_dims(features, axis=2)
        pred_probs = model.predict(features)
        pred_label = encoder2.inverse_transform(pred_probs)[0][0]
        # Example mapping (customize as per your model's emotion-to-VAD mapping):
        vad_dict = {
            'angry':    [0.1, 0.9, 0.7],
            'calm':     [0.7, 0.2, 0.6],
            'disgust':  [0.1, 0.8, 0.3],
            'fear':     [0.1, 0.1, 0.2],
            'happy':    [0.9, 0.8, 0.8],
            'neutral':  [0.5, 0.5, 0.5],
            'sad':      [0.2, 0.3, 0.4],
            'surprise': [0.8, 0.8, 0.6],
        }
        vad = vad_dict.get(pred_label, [0.5, 0.5, 0.5])
        valence_list.append(vad[0])
        arousal_list.append(vad[1])
        dominance_list.append(vad[2])
        vad_times.append(start + window/2)
        start += step
    return vad_times, valence_list, arousal_list, dominance_list