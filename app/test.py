from flask import Flask, request, render_template, jsonify, session, redirect, url_for
from send_mail_util import send_email

import os
from analytics import PROMPTS, generate_section
from emotion_inference import calculate_average_speech_rate, get_calm_score, get_vad_over_time
from context_based import get_agent_employee_sentiment, parse_transcript_lines, get_sentiment_flow

from bson.objectid import ObjectId
from datetime import datetime
from dotenv import load_dotenv
import mysql.connector
import json

load_dotenv()


conn = mysql.connector.connect(
    host='localhost',      # your MySQL host
    user='root',  # your MySQL username
    password='Subhash@2003',  # your MySQL password
    database='aivqa'   # your database name
)

cursor = conn.cursor()
cursor.execute("SELECT * FROM calls")
rows = cursor.fetchall()

app =Flask()

@app.route('/dashboard')
def dashboard():
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM calls ORDER BY created_at DESC")
    rows = cursor.fetchall()

    call_list = []

    # --- Helper: extract agent name from transcript ---
    def extract_agent_name(transcript):
        try:
            lines = transcript.splitlines()
            for line in lines:
                if 'Agent' in line:
                    parts = line.split('Agent')
                    if len(parts) > 1:
                        name_part = parts[1].strip('():- ')
                        return name_part.split()[0]
        except:
            pass
        return "Unknown"

    # --- Helper: map sentiment percent to label + emoji ---
    def get_sentiment_label_and_icon(percent):
        if percent is None:
            return ("N/A", "")
        if percent >= 66:
            return ("Positive", "😊")
        elif percent >= 45:
            return ("Neutral", "😐")
        else:
            return ("Negative", "😞")

    for row in rows:
        (
            _id,
            agent_audio,
            employee_audio,
            transcript,
            analysis_json,
            segment_times_agent,
            segment_zcrs_agent,
            segment_times_employee,
            segment_zcrs_employee,
            avg_speech_rate,
            calm_score,
            vad_times,
            valence_list,
            arousal_list,
            dominance_list,
            agent_sentiment_percent,
            employee_sentiment_percent,
            sentiment_flow,
            overall_score,
            duration,
            department,
            topic,
            agent_name,
            call_id,
            user_name,
            created_at,
            call_id_int
        ) = row

        # Parse JSON fields safely
        try:
            analysis = json.loads(analysis_json) if analysis_json else {}
        except:
            analysis = {}

        call_summary = (analysis.get("call_summary") or {})

        sentiment_label, sentiment_icon = get_sentiment_label_and_icon(agent_sentiment_percent)

        # Ensure we have a call_id (fallback to regex on filenames if needed)
        if not call_id:
            match = re.search(r"(\d{6})", agent_audio or "")
            if not match:
                match = re.search(r"(\d{6})", employee_audio or "")
            call_id = match.group(1) if match else "N/A"

        # SOP adherence
        sop_steps_followed = analysis.get("sop_adherence")
        if sop_steps_followed:
            sop_steps_followed = sum(1 for s in sop_steps_followed.values() if s)
        else:
            sop_steps_followed = 0

        call_list.append({
            "_id": str(_id),
            "call_id": call_id,
            "timestamp": created_at,
            "duration": duration if duration else "N/A",
            "agent_name": agent_name or extract_agent_name(transcript),
            "department": department or call_summary.get("department", "N/A"),
            "topic": topic or call_summary.get("topic", "N/A"),
            "resolved": call_summary.get("resolved", "N/A"),
            "sentiment": sentiment_label,
            "sentiment_icon": sentiment_icon,
            "overall_score": overall_score or "N/A",
            "valence_list": json.loads(valence_list) if valence_list else [],
            "arousal_list": json.loads(arousal_list) if arousal_list else [],
            "agent_speech_rate": avg_speech_rate or 0,
            "voice_elevation_freq": calm_score or "N/A",  # used calm_score as proxy
            "sop_steps_followed": sop_steps_followed
        })

    return render_template('dashboard.html', user=session.get("user"), call_list=call_list)
