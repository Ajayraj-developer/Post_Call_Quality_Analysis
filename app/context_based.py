import os
import numpy as np
import re
from transformers import pipeline as hf_pipeline
import torch

# Force PyTorch backend and avoid TensorFlow issues
pipeline = hf_pipeline(
    "sentiment-analysis", 
    model="cardiffnlp/twitter-roberta-base-sentiment-latest",
    framework="pt"  # Explicitly use PyTorch
)

def predict_sentiment(text, pipeline):
    result = pipeline(text)[0]
    label = result['label']
    score = result['score']
    if label.upper() in ['POSITIVE', 'LABEL_2']:
        sentiment = 'Positive'
        norm_score = score
    elif label.upper() in ['NEGATIVE', 'LABEL_0']:
        sentiment = 'Negative'
        norm_score = 1 - score
    else:
        sentiment = 'Neutral'
        norm_score = 0.5
    return sentiment, norm_score

def get_role_from_text(text, speaker):
    text_lower = text.lower()
    speaker_lower = speaker.lower() if speaker else ''
    if 'agent' in speaker_lower:
        return 'agent'
    elif 'employee' in speaker_lower:
        return 'employee'
    match = re.match(r'^\s*\d{1,2}:\d{2}:\d{2}\s*[–-]\s*(agent|employee)', text_lower)
    if match:
        return match.group(1)
    match2 = re.match(r'^\s*\d{1,2}:\d{2}:\d{2}\s*[–-]\s*(agent|employee)\s*\(', text_lower)
    if match2:
        return match2.group(1)
    match3 = re.match(r'^(agent|employee)', text_lower)
    if match3:
        return match3.group(1)
    match4 = re.search(r'\((agent|employee)\)', text_lower)
    if match4:
        return match4.group(1)
    match5 = re.match(r'^\s*[|\-]\s*(agent|employee)\s*[|\-]', text_lower)
    if match5:
        return match5.group(1)
    return None

def get_agent_employee_sentiment(transcript):
    agent_scores = []
    employee_scores = []
    for msg in transcript:
        sentiment, score = predict_sentiment(msg['text'], pipeline)
        role = get_role_from_text(msg['text'], msg['speaker'])
        if role == 'agent':
            agent_scores.append(score)
        elif role == 'employee':
            employee_scores.append(score)
    agent_percent = int(round(np.mean(agent_scores) * 100)) if agent_scores else 0
    employee_percent = int(round(np.mean(employee_scores) * 100)) if employee_scores else 0
    return {
        'agent_sentiment_percent': agent_percent,
        'employee_sentiment_percent': employee_percent
    }

def parse_transcript_lines(transcript_text):
    lines = transcript_text.strip().splitlines()
    transcript = []
    i = 0
    while i < len(lines):
        match = re.match(r'^(\d{1,2}:\d{2}:\d{2})\s*[–-]\s*([^:]+):\s*$', lines[i])
        if match:
            speaker = match.group(2).strip()
            if i + 1 < len(lines):
                text = lines[i].strip() + ' ' + lines[i+1].strip()
                transcript.append({"speaker": speaker, "text": text})
                i += 2
            else:
                transcript.append({"speaker": speaker, "text": lines[i].strip()})
                i += 1
        else:
            i += 1
    return transcript

def get_sentiment_flow(transcript):
    flow = []
    for i, msg in enumerate(transcript):
        sentiment, score = predict_sentiment(msg['text'], pipeline)
        match = re.match(r'^(\d{1,2}:\d{2}:\d{2})', msg['text'])
        time = match.group(1) if match else str(i+1)
        flow.append({
            'time': time,
            'speaker': get_role_from_text(msg['text'], msg['speaker']).capitalize() if get_role_from_text(msg['text'], msg['speaker']) else msg['speaker'],
            'text': msg['text'],
            'sentiment': sentiment,
            'score': score
        })
    return flow

__all__ = [
    'predict_sentiment',
    'get_role_from_text',
    'get_agent_employee_sentiment',
    'parse_transcript_lines',
    'get_sentiment_flow',
]

if __name__ == "__main__":
    transcript_text = '''
13:50:10 – Agent (Mia):
Hello, this is Mia from TechHelp Support. How can I assist you today?
13:50:16 – Employee:
Hi Mia, I’m having trouble accessing the new internal portal. I’m not sure if my permissions were set up correctly.
13:50:21 – Agent (Mia):
I’m glad you reached out! Let me take a quick look at your account and see what’s going on.
13:50:26 – Employee:
Thanks so much. I’ve heard good things about the new system and I’m eager to start using it.
13:50:31 – Agent (Mia):
I appreciate your enthusiasm! Alright, I see the issue — your role wasn’t properly assigned. I’ll fix that now.
13:50:36 – Employee:
That’s great. I really appreciate how fast you’re handling this.
13:50:43 – Agent (Mia):
You're very welcome! I’ve updated your permissions. Can you try logging in again and let me know if it works?
13:50:50 – Employee:
Just tried it — and yes, I’m in! Everything looks good on my end now.
13:51:01 – Agent (Mia):
Awesome! If you run into anything else or have questions about features, don’t hesitate to contact us.
13:51:11 – Employee:
Will do. Thanks again for your help, Mia. This was a great experience.
13:51:17 – Agent (Mia):
My pleasure! Have a productive day ahead.
'''
    transcript = parse_transcript_lines(transcript_text)
    sentiment = get_agent_employee_sentiment(transcript)
    print("Final Sentiment:", sentiment)
