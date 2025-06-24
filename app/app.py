from flask import Flask, request, render_template, jsonify
import os
from analytics import PROMPTS, generate_section
from emotion_inference import calculate_average_speech_rate, get_calm_score, get_vad_over_time
from context_based import get_agent_employee_sentiment, parse_transcript_lines, get_sentiment_flow
from cloud import sync_latest_call_from_cloud

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'static/uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

@app.route('/', methods=['GET', 'POST'])
def upload_audio():
    if request.method == 'POST':
        audio_file_agent = request.files.get('audio_file_agent')
        audio_file_employee = request.files.get('audio_file_employee')
        transcript_file = request.files.get('transcript_file')

        if not audio_file_agent or audio_file_agent.filename == '':
            return "No agent audio file uploaded", 400
        if not audio_file_employee or audio_file_employee.filename == '':
            return "No employee audio file uploaded", 400
        if not transcript_file or transcript_file.filename == '':
            return "No transcript file uploaded", 400

        # Save audio files
        audio_path_agent = os.path.join(app.config['UPLOAD_FOLDER'], audio_file_agent.filename)
        audio_file_agent.save(audio_path_agent)
        audio_path_employee = os.path.join(app.config['UPLOAD_FOLDER'], audio_file_employee.filename)
        audio_file_employee.save(audio_path_employee)

        # Read transcript
        transcript = transcript_file.read().decode('utf-8')

        # Parse transcript using robust parser for multi-line, timestamped format
        transcript_msgs = parse_transcript_lines(transcript)

        # Get agent and employee sentiment scores
        sentiment_scores = get_agent_employee_sentiment(transcript_msgs)
        agent_sentiment_percent = sentiment_scores['agent_sentiment_percent']
        employee_sentiment_percent = sentiment_scores['employee_sentiment_percent']

        # Generate analysis sections
        analysis = {}
        if transcript.strip():
            for section, prompt in PROMPTS.items():
                result = generate_section(prompt, transcript)
                if result:
                    analysis[section] = result
                else:
                    analysis[section] = {"error": f"Failed to parse {section} output"}

        # Calculate speech rate (ZCR) for both audios
        avg_speech_rate_agent, segment_times_agent, segment_zcrs_agent = calculate_average_speech_rate(audio_path_agent)
        avg_speech_rate_employee, segment_times_employee, segment_zcrs_employee = calculate_average_speech_rate(audio_path_employee)
        
        # Use overlapped/mixed audio for tone and VAD analysis
        mixed_audio_path = os.path.join(app.config['UPLOAD_FOLDER'], 'mixed_audio.wav')
        if os.path.exists(mixed_audio_path):
            calm_score = get_calm_score(mixed_audio_path)
            vad_times, valence_list, arousal_list, dominance_list = get_vad_over_time(mixed_audio_path)
        else:
            # fallback to agent audio if mixed not found
            calm_score = get_calm_score(audio_path_agent)
            vad_times, valence_list, arousal_list, dominance_list = get_vad_over_time(audio_path_agent)

        # Get per-message sentiment flow for graph
        sentiment_flow = get_sentiment_flow(transcript_msgs)

        # --- Calculate Overall Score (out of 10) ---
        # 40% context (overall sentiment, average of agent+employee), 30% tone, 10% SOP, 10% agent, 10% resolved
        context_score = (agent_sentiment_percent + employee_sentiment_percent) / 2 / 10 if agent_sentiment_percent is not None and employee_sentiment_percent is not None else 0  # scale to 0-10
        tone_score = calm_score if calm_score is not None else 0  # already out of 10
        sop_score = 0
        if analysis.get('sop_adherence'):
            sop = analysis['sop_adherence']
            sop_steps = [sop.get('identity_verification'), sop.get('security_questions'), sop.get('two_factor_enabled'), sop.get('account_activity_review'), sop.get('security_best_practices')]
            sop_score = (sum(1 for s in sop_steps if s) / 5) * 1  # out of 1
        agent_score = agent_sentiment_percent / 100 if agent_sentiment_percent is not None else 0  # out of 1
        resolved_score = 0
        if analysis.get('call_summary') and analysis['call_summary'].get('resolved'):
            resolved_score = 1 if str(analysis['call_summary']['resolved']).strip().lower() == 'yes' else 0
        overall_score = (
            0.4 * context_score +
            0.3 * tone_score +
            1.0 * sop_score +
            1.0 * agent_score +
            1.0 * resolved_score
        )

        # --- Debug output for overall score calculation ---
        print('--- Overall Score Debug ---')
        print(f'Context score (40%): {context_score:.2f}')
        print(f'Tone score (30%): {tone_score:.2f}')
        print(f'SOP adherence score (10%): {sop_score:.2f}')
        print(f'Agent sentiment score (10%): {agent_score:.2f}')
        print(f'Resolved score (10%): {resolved_score:.2f}')
        print(f'Final overall score: {overall_score:.2f}')
        print('--------------------------')

        return render_template(
            'new.html',
            transcript=transcript,
            analysis=analysis,
            audio_file_agent=audio_file_agent.filename,
            audio_file_employee=audio_file_employee.filename,
            segment_times_agent=segment_times_agent,
            segment_zcrs_agent=segment_zcrs_agent,
            segment_times_employee=segment_times_employee,
            segment_zcrs_employee=segment_zcrs_employee,
            avg_speech_rate=avg_speech_rate_agent,  # keep for now
            calm_score=calm_score,
            vad_times=vad_times,
            valence_list=valence_list,
            arousal_list=arousal_list,
            dominance_list=dominance_list,
            agent_sentiment_percent=agent_sentiment_percent,
            employee_sentiment_percent=employee_sentiment_percent,
            sentiment_flow=sentiment_flow,
            overall_score=overall_score,
        )

    return render_template('upload.html')

@app.route('/sync_cloud', methods=['POST'])
def sync_cloud():
    # Set your Google Drive folder ID and download directory
    folder_id = '1F5JG1JCG94Pkuj5CPsevbk1x_BqWg5vJ'  # TODO: move to config if needed
    download_dir = os.path.join('Cloud_sync', 'downloaded_transcripts')
    os.makedirs(download_dir, exist_ok=True)
    files, call_id = sync_latest_call_from_cloud(folder_id, download_dir)
    if not files:
        return "No call files found in cloud folder.", 404

    # Get file paths
    transcript_path = files.get(f"{call_id}.txt")
    agent_audio_path = files.get(f"{call_id}_agent.wav")
    emp_audio_path = files.get(f"{call_id}_emp.wav")
    if not (transcript_path and agent_audio_path and emp_audio_path):
        return "Could not find all required files for the latest call.", 400

    # Read transcript
    with open(transcript_path, 'r', encoding='utf-8') as f:
        transcript = f.read()
    transcript_msgs = parse_transcript_lines(transcript)
    sentiment_scores = get_agent_employee_sentiment(transcript_msgs)
    agent_sentiment_percent = sentiment_scores['agent_sentiment_percent']
    employee_sentiment_percent = sentiment_scores['employee_sentiment_percent']

    analysis = {}
    if transcript.strip():
        for section, prompt in PROMPTS.items():
            result = generate_section(prompt, transcript)
            if result:
                analysis[section] = result
            else:
                analysis[section] = {"error": f"Failed to parse {section} output"}

    avg_speech_rate_agent, segment_times_agent, segment_zcrs_agent = calculate_average_speech_rate(agent_audio_path)
    avg_speech_rate_employee, segment_times_employee, segment_zcrs_employee = calculate_average_speech_rate(emp_audio_path)
    mixed_audio_path = None  # Not available from cloud, fallback to agent audio
    calm_score = get_calm_score(agent_audio_path)
    vad_times, valence_list, arousal_list, dominance_list = get_vad_over_time(agent_audio_path)
    sentiment_flow = get_sentiment_flow(transcript_msgs)

    context_score = (agent_sentiment_percent + employee_sentiment_percent) / 2 / 10 if agent_sentiment_percent is not None and employee_sentiment_percent is not None else 0
    tone_score = calm_score if calm_score is not None else 0
    sop_score = 0
    if analysis.get('sop_adherence'):
        sop = analysis['sop_adherence']
        sop_steps = [sop.get('identity_verification'), sop.get('security_questions'), sop.get('two_factor_enabled'), sop.get('account_activity_review'), sop.get('security_best_practices')]
        sop_score = (sum(1 for s in sop_steps if s) / 5) * 1
    agent_score = agent_sentiment_percent / 100 if agent_sentiment_percent is not None else 0
    resolved_score = 0
    if analysis.get('call_summary') and analysis['call_summary'].get('resolved'):
        resolved_score = 1 if str(analysis['call_summary']['resolved']).strip().lower() == 'yes' else 0
    overall_score = (
        0.4 * context_score +
        0.3 * tone_score +
        1.0 * sop_score +
        1.0 * agent_score +
        1.0 * resolved_score
    )

    return render_template(
        'new.html',
        transcript=transcript,
        analysis=analysis,
        audio_file_agent=os.path.basename(agent_audio_path),
        audio_file_employee=os.path.basename(emp_audio_path),
        segment_times_agent=segment_times_agent,
        segment_zcrs_agent=segment_zcrs_agent,
        segment_times_employee=segment_times_employee,
        segment_zcrs_employee=segment_zcrs_employee,
        avg_speech_rate=avg_speech_rate_agent,
        calm_score=calm_score,
        vad_times=vad_times,
        valence_list=valence_list,
        arousal_list=arousal_list,
        dominance_list=dominance_list,
        agent_sentiment_percent=agent_sentiment_percent,
        employee_sentiment_percent=employee_sentiment_percent,
        sentiment_flow=sentiment_flow,
        overall_score=overall_score,
    )

if __name__ == '__main__':
    app.run(debug=True)