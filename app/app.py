from flask import Flask, request, render_template, jsonify, session, redirect, url_for
import msal
import json
from functools import wraps
import os
from analytics import PROMPTS, generate_section
from emotion_inference import calculate_average_speech_rate, get_calm_score, get_vad_over_time
from context_based import get_agent_employee_sentiment, parse_transcript_lines, get_sentiment_flow
from cloud import sync_latest_call_from_cloud
from pymongo import MongoClient
from bson.objectid import ObjectId
from datetime import datetime
import glob
import wave
import re
from apscheduler.schedulers.background import BackgroundScheduler
import certifi
from pydub import AudioSegment
from dotenv import load_dotenv
load_dotenv()


# Microsoft Authentication Configuration
CLIENT_ID = "9297e893-98da-423c-8c1f-24c626c6c47a"
CLIENT_SECRET = os.environ.get("CLIENT_SECRET")
AUTHORITY = "https://login.microsoftonline.com/f5791d91-daca-4d28-8700-680f7a2f8b6a"
REDIRECT_PATH = "/getAToken"
SCOPE = ["User.ReadBasic.All"]
REDIRECT_URI = "http://localhost:5000/getAToken"

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'pxJ8Q~BxiUUQXC0ngGcm8FWIntjMvFRPMhQOWcrG')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
TOKEN_CACHE_DIR = 'token_cache'
os.makedirs(TOKEN_CACHE_DIR, exist_ok=True)

def _get_cache_file_path():
    session_id = session.get('session_id')
    if not session_id:
        import uuid
        session_id = str(uuid.uuid4())
        session['session_id'] = session_id
    return os.path.join(TOKEN_CACHE_DIR, f'token_cache_{session_id}.json')

def _load_cache():
    cache = msal.SerializableTokenCache()
    cache_file = _get_cache_file_path()
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'r') as f:
                cache.deserialize(f.read())
        except (json.JSONDecodeError, IOError):
            pass
    return cache

def _save_cache(cache):
    if cache.has_state_changed:
        cache_file = _get_cache_file_path()
        try:
            with open(cache_file, 'w') as f:
                f.write(cache.serialize())
        except IOError:
            pass

def _build_msal_app(cache=None, authority=None):
    return msal.ConfidentialClientApplication(
        CLIENT_ID,
        authority=authority or AUTHORITY,
        client_credential=CLIENT_SECRET,
        token_cache=cache
    )

def _build_auth_code_flow(authority=None, scopes=None):
    return _build_msal_app(authority=authority).initiate_auth_code_flow(
        scopes or [],
        redirect_uri=REDIRECT_URI
    )

def _get_token_from_cache(scope=None):
    cache = _load_cache()
    cca = _build_msal_app(cache=cache)
    accounts = cca.get_accounts()
    if accounts:
        result = cca.acquire_token_silent(scope or [], account=accounts[0])
        _save_cache(cache)
        return result

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("user"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function

def _get_flow_file_path():
    session_id = session.get('session_id')
    if not session_id:
        import uuid
        session_id = str(uuid.uuid4())
        session['session_id'] = session_id
    return os.path.join(TOKEN_CACHE_DIR, f'flow_{session_id}.json')

def _save_flow(flow):
    flow_file = _get_flow_file_path()
    try:
        with open(flow_file, 'w') as f:
            json.dump(flow, f)
    except IOError:
        pass

def _load_flow():
    flow_file = _get_flow_file_path()
    if os.path.exists(flow_file):
        try:
            with open(flow_file, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return None

@app.route("/login")
def login():
    flow = _build_auth_code_flow(scopes=SCOPE)
    _save_flow(flow)
    return render_template("login.html", auth_url=flow["auth_uri"])

@app.route(REDIRECT_PATH)
def authorized():
    try:
        cache = _load_cache()
        flow = _load_flow()
        if not flow:
            return redirect(url_for("login"))
        result = _build_msal_app(cache=cache).acquire_token_by_auth_code_flow(
            flow, request.args)
        if "error" in result:
            return render_template("auth_error.html", result=result)
        user_info = result.get("id_token_claims", {})
        session["user"] = {
            "name": user_info.get("name", "Unknown"),
            "preferred_username": user_info.get("preferred_username", ""),
            "oid": user_info.get("oid", "")
        }
        _save_cache(cache)
        try:
            flow_file = _get_flow_file_path()
            if os.path.exists(flow_file):
                os.remove(flow_file)
        except Exception:
            pass
    except Exception as e:
        return redirect(url_for("login"))
    return redirect(url_for("upload_audio"))

@app.route("/logout")
def logout():
    try:
        cache_file = _get_cache_file_path()
        if os.path.exists(cache_file):
            os.remove(cache_file)
        flow_file = _get_flow_file_path()
        if os.path.exists(flow_file):
            os.remove(flow_file)
    except Exception:
        pass
    session.clear()
    return redirect(
        AUTHORITY + "/oauth2/v2.0/logout" +
        "?post_logout_redirect_uri=" + url_for("login", _external=True, _scheme="http", _server="localhost:5000"))

@app.route('/', methods=['GET', 'POST'])
@login_required
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

        # Calculate duration (in seconds) for agent audio (as a proxy for call duration)
        def get_audio_duration(filepath):
            try:
                audio = AudioSegment.from_file(filepath)
                return round(len(audio) / 1000, 2)  # duration in seconds
            except Exception:
                return None
        duration = get_audio_duration(audio_path_agent)

        # Extract department and topic from analysis if available
        department = None
        topic = None
        if analysis.get('call_summary'):
            department = analysis['call_summary'].get('department')
            topic = analysis['call_summary'].get('topic')

        # Extract agent name from transcript using extract_agent_name logic from new_app.py
        def extract_agent_name(transcript):
            try:
                lines = transcript.splitlines()
                for line in lines:
                    if 'Agent' in line:
                        parts = line.split('Agent')
                        if len(parts) > 1:
                            name_part = parts[1].strip('():- ')
                            return name_part.split()[0]  # just the name
            except:
                pass
            return "Unknown"
        agent_name = extract_agent_name(transcript)

        # Extract timestamps from transcript (first and last timestamp for call duration)
        dash_chars = r"[-–—‒−]"
        timestamps = []
        for line in transcript.splitlines():
            match = re.match(r"(\d{2}:\d{2}:\d{2})\s*" + dash_chars, line)
            if match:
                timestamps.append(match.group(1))
        call_start = timestamps[0] if timestamps else datetime.utcnow().strftime('%H:%M:%S')
        call_end = timestamps[-1] if len(timestamps) > 1 else None

        # Extract 6-digit call id from agent audio or employee audio filename
        call_id_val = None
        match = re.search(r"(\d{6})", os.path.basename(audio_path_agent))
        if not match:
            match = re.search(r"(\d{6})", os.path.basename(audio_path_employee))
        if match:
            call_id_val = match.group(1)
        else:
            call_id_val = "N/A"

        # Calculate frequency of voice elevation (arousal > threshold)
        arousal_threshold = 0.85
        voice_elevation_freq = sum(1 for a in arousal_list if a > arousal_threshold)

        # Calculate SOP steps followed
        sop_steps_followed = 0
        if analysis.get('sop_adherence'):
            sop = analysis['sop_adherence']
            sop_steps = [
                sop.get('identity_verification'),
                sop.get('security_questions'),
                sop.get('two_factor_enabled'),
                sop.get('account_activity_review'),
                sop.get('security_best_practices')
            ]
            sop_steps_followed = sum(1 for s in sop_steps if s)
        # Save all analysis and metadata to MongoDB (just like manual upload)
        doc = {
            "agent_audio": os.path.basename(audio_path_agent),
            "employee_audio": os.path.basename(audio_path_employee),
            "transcript": transcript,
            "analysis": analysis,
            "segment_times_agent": segment_times_agent,
            "segment_zcrs_agent": segment_zcrs_agent,
            "segment_times_employee": segment_times_employee,
            "segment_zcrs_employee": segment_zcrs_employee,
            "avg_speech_rate": avg_speech_rate_agent,
            "calm_score": calm_score,
            "vad_times": vad_times,
            "valence_list": valence_list,
            "arousal_list": arousal_list,
            "dominance_list": dominance_list,
            "agent_sentiment_percent": agent_sentiment_percent,
            "employee_sentiment_percent": employee_sentiment_percent,
            "sentiment_flow": sentiment_flow,
            "overall_score": overall_score,
            "duration": duration,
            "department": department,
            "topic": topic,
            "agent_name": agent_name,
            "call_id": call_id_val,
            "user_name": session.get("user", {}).get("name", "Unknown"),
            "user_email": session.get("user", {}).get("preferred_username", ""),
            "created_at": datetime.utcnow(),
            "call_start": call_start,
            "call_end": call_end,
            "voice_elevation_freq": voice_elevation_freq,
            "sop_steps_followed": sop_steps_followed
        }
        try:
            result = calls_collection.insert_one(doc)
        except Exception as e:
            if 'duplicate key error' in str(e):
                print(f"[DEDUP] Duplicate call_id {doc.get('call_id')} not inserted.")
                result = None
            else:
                print(f"[ERROR] Exception inserting doc: {e}")
                errors = []  # Ensure errors is defined
                errors.append(f"Error inserting doc: {str(e)}")
                result = None
        if result:
            call_id = str(result.inserted_id)
            return redirect(url_for('view_call', call_id=call_id))
        else:
            return "Error inserting document into database.", 500

    # Fetch call list for display
    call_list = []
    for call in calls_collection.find().sort("created_at", -1):
        transcript = call.get("transcript", "")
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
        agent_name = extract_agent_name(transcript)
        call_summary = (call.get("analysis") or {}).get("call_summary", {})
        
        def get_sentiment_label_and_icon(percent):
            if percent is None:
                return ("N/A", "")
            if percent >= 66:
                return ("Positive", "😊")
            elif percent >= 45:
                return ("Neutral", "😐") 
            else:
                return ("Negative", "😞")

        agent_sentiment_percent = call.get("agent_sentiment_percent")
        sentiment_label, sentiment_icon = get_sentiment_label_and_icon(agent_sentiment_percent)
        # Extract 6-digit call id from agent audio or transcript filename
        call_id = None
        agent_audio = call.get("agent_audio", "")
        match = re.search(r"(\d{6})", agent_audio)
        if not match:
            emp_audio = call.get("employee_audio", "")
            match = re.search(r"(\d{6})", emp_audio)
        if not match:
            pass
        if match:
            call_id = match.group(1)
        else:
            call_id = "N/A"
        call_list.append({
            "_id": str(call.get("_id")),
            "call_id": call_id,
            "timestamp": call.get("created_at"),  # Use actual sync time
            "duration": call.get("duration", "N/A"),
            "agent_name": agent_name,
            "department": call.get("department", call_summary.get("department", "N/A")),
            "topic": call.get("topic", call_summary.get("topic", "N/A")),
            "resolved": call_summary.get("resolved", "N/A"),
            "sentiment": sentiment_label,
            "sentiment_icon": sentiment_icon,
            "overall_score": call.get("overall_score", "N/A"),
            # Add any other fields needed for the table
            # "timestamp": datetime.now()  # Removed this line
        })
    return render_template('upload.html', user=session.get("user"), call_list=call_list)

@app.route('/dashboard')
@login_required
def dashboard():
    # Fetch call list for display (same as before, but for dashboard)
    call_list = []
    for call in calls_collection.find().sort("created_at", -1):
        transcript = call.get("transcript", "")
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
        agent_name = extract_agent_name(transcript)
        call_summary = (call.get("analysis") or {}).get("call_summary", {})
        def get_sentiment_label_and_icon(percent):
            if percent is None:
                return ("N/A", "")
            if percent >= 66:
                return ("Positive", "😊")
            elif percent >= 45:
                return ("Neutral", "😐")
            else:
                return ("Negative", "😞")
        agent_sentiment_percent = call.get("agent_sentiment_percent")
        sentiment_label, sentiment_icon = get_sentiment_label_and_icon(agent_sentiment_percent)
        import re
        call_id = None
        agent_audio = call.get("agent_audio", "")
        match = re.search(r"(\d{6})", agent_audio)
        if not match:
            emp_audio = call.get("employee_audio", "")
            match = re.search(r"(\d{6})", emp_audio)
        if not match:
            pass
        if match:
            call_id = match.group(1)
        else:
            call_id = "N/A"
        # Use sop_steps_followed from doc if present, else fallback to calculation from analysis
        sop_steps_followed = call.get('sop_steps_followed')
        if sop_steps_followed is None:
            sop = (call.get("analysis") or {}).get("sop_adherence", {})
            sop_steps = [
                sop.get('identity_verification'),
                sop.get('security_questions'),
                sop.get('two_factor_enabled'),
                sop.get('account_activity_review'),
                sop.get('security_best_practices')
            ]
            sop_steps_followed = sum(1 for s in sop_steps if s)
        call_list.append({
            "_id": str(call.get("_id")),
            "call_id": call_id,
            "timestamp": call.get("created_at"),
            "duration": call.get("duration", "N/A"),
            "agent_name": agent_name,
            "department": call.get("department", call_summary.get("department", "N/A")),
            "topic": call.get("topic", call_summary.get("topic", "N/A")),
            "resolved": call_summary.get("resolved", "N/A"),
            "sentiment": sentiment_label,
            "sentiment_icon": sentiment_icon,
            "overall_score": call.get("overall_score", "N/A"),
            "valence_list": call.get("valence_list", []),
            "arousal_list": call.get("arousal_list", []),
            "agent_speech_rate": call.get("avg_speech_rate", 0),
            "voice_elevation_freq": call.get("voice_elevation_freq", "N/A"),
            "sop_steps_followed": sop_steps_followed
        })
    
    return render_template('dashboard.html', user=session.get("user"), call_list=call_list)
@app.route('/team')
def team():
    return render_template('team.html')
@app.route('/faq')
@login_required
def faq():
    return render_template('faq.html', user=session.get("user"))
@app.route('/sync_cloud', methods=['POST'])
@login_required
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

    # Calculate duration (in seconds) for agent audio (as a proxy for call duration)
    import wave
    def get_audio_duration(filepath):
        try:
            with wave.open(filepath, 'rb') as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                return round(frames / float(rate), 2)
        except Exception:
            return None
    duration = get_audio_duration(agent_audio_path)

    # Extract department and topic from analysis if available
    department = None
    topic = None
    if analysis.get('call_summary'):
        department = analysis['call_summary'].get('department')
        topic = analysis['call_summary'].get('topic')

    # Extract agent name from transcript using extract_agent_name logic from new_app.py
    def extract_agent_name(transcript):
        try:
            lines = transcript.splitlines()
            for line in lines:
                if 'Agent' in line:
                    parts = line.split('Agent')
                    if len(parts) > 1:
                        name_part = parts[1].strip('():- ')
                        return name_part.split()[0]  # just the name
        except:
            pass
        return "Unknown"
    agent_name = extract_agent_name(transcript)

    # Extract timestamps from transcript (first and last timestamp for call duration)
    dash_chars = r"[-–—‒−]"
    timestamps = []
    for line in transcript.splitlines():
        match = re.match(r"(\d{2}:\d{2}:\d{2})\s*" + dash_chars, line)
        if match:
            timestamps.append(match.group(1))
    call_start = timestamps[0] if timestamps else datetime.utcnow().strftime('%H:%M:%S')
    call_end = timestamps[-1] if len(timestamps) > 1 else None

    # Extract 6-digit call id from agent audio or employee audio filename
    call_id_val = None
    match = re.search(r"(\d{6})", os.path.basename(agent_audio_path))
    if not match:
        match = re.search(r"(\d{6})", os.path.basename(emp_audio_path))
    if match:
        call_id_val = match.group(1)
    else:
        call_id_val = "N/A"

    # Avoid duplicate import: check if already in DB by call_id
    if call_id_val != "N/A" and calls_collection.find_one({"call_id": call_id_val}):
        print(f"[CLOUD SYNC] Skipping duplicate set by call_id: {call_id_val}")
        return jsonify({"status": "duplicate", "call_id": call_id_val}), 200

    # Save all analysis and metadata to MongoDB (just like manual upload)
    doc = {
        "agent_audio": os.path.basename(agent_audio_path),
        "employee_audio": os.path.basename(emp_audio_path),
        "transcript": transcript,
        "analysis": analysis,
        "segment_times_agent": segment_times_agent,
        "segment_zcrs_agent": segment_zcrs_agent,
        "segment_times_employee": segment_times_employee,
        "segment_zcrs_employee": segment_zcrs_agent,
        "avg_speech_rate": avg_speech_rate_agent,
        "calm_score": calm_score,
        "vad_times": vad_times,
        "valence_list": valence_list,
        "arousal_list": arousal_list,
        "dominance_list": dominance_list,
        "agent_sentiment_percent": agent_sentiment_percent,
        "employee_sentiment_percent": employee_sentiment_percent,
        "sentiment_flow": sentiment_flow,
        "overall_score": overall_score,
        "duration": duration,
        "department": department,
        "topic": topic,
        "agent_name": agent_name,
        "call_id": call_id_val,
        "user_name": session.get("user", {}).get("name", "Unknown"),
        "user_email": session.get("user", {}).get("preferred_username", ""),
        "created_at": datetime.utcnow(),
        "call_start": call_start,
        "call_end": call_end
    }
    try:
        calls_collection.insert_one(doc)
    except Exception as e:
        if 'duplicate key error' in str(e):
            print(f"[DEDUP] Duplicate call_id {doc.get('call_id')} not inserted.")
        else:
            print(f"[ERROR] Exception inserting doc: {e}")
            errors = []
            errors.append(f"Error inserting doc: {str(e)}")

    # Delete synced files from downloaded_transcripts folder
    for f in [transcript_path, agent_audio_path, emp_audio_path]:
        try:
            if f and os.path.exists(f):
                os.remove(f)
                print(f"[CLOUD SYNC] Deleted file: {f}")
        except Exception as e:
            print(f"[CLOUD SYNC][ERROR] Could not delete file {f}: {e}")

    return render_template(
        'new.html',
        transcript=transcript,
        analysis=analysis,
        audio_file_agent=os.path.basename(agent_audio_path),
        audio_file_employee=os.path.basename(emp_audio_path),
        segment_times_agent=segment_times_agent,
        segment_zcrs_agent=segment_zcrs_agent,
        segment_times_employee=segment_times_employee,
        segment_zcrs_employee=segment_zcrs_agent,
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
        user=session.get("user"),
    )

@app.route('/call/<call_id>')
@login_required
def view_call(call_id):
    call = calls_collection.find_one({"_id": ObjectId(call_id)})
    if not call:
        return "Call not found", 404
    return render_template(
        'new.html',
        transcript=call.get('transcript'),
        analysis=call.get('analysis'),
        audio_file_agent=call.get('agent_audio'),
        audio_file_employee=call.get('employee_audio'),
        segment_times_agent=call.get('segment_times_agent'),
        segment_zcrs_agent=call.get('segment_zcrs_agent'),
        segment_times_employee=call.get('segment_times_employee'),
        segment_zcrs_employee=call.get('segment_zcrs_employee'),
        avg_speech_rate=call.get('avg_speech_rate'),
        calm_score=call.get('calm_score'),
        vad_times=call.get('vad_times'),
        valence_list=call.get('valence_list'),
        arousal_list=call.get('arousal_list'),
        dominance_list=call.get('dominance_list'),
        agent_sentiment_percent=call.get('agent_sentiment_percent'),
        employee_sentiment_percent=call.get('employee_sentiment_percent'),
        sentiment_flow=call.get('sentiment_flow'),
        overall_score=call.get('overall_score'),
        user=session.get("user"),
    )

@app.route('/sync_local', methods=['POST'])
@login_required
def sync_local():
    local_dir = os.path.join(os.path.dirname(__file__), 'local_sync')
    txt_files = glob.glob(os.path.join(local_dir, '*.txt'))
    print(f"[DEBUG] Scanning folder: {local_dir}")
    print(f"[DEBUG] Found transcript files: {txt_files}")
    processed = 0
    errors = []
    for txt_path in txt_files:
        try:
            base = os.path.splitext(os.path.basename(txt_path))[0]
            agent_audio_path = os.path.join(local_dir, f'{base}_agent.wav')
            emp_audio_path = os.path.join(local_dir, f'{base}_emp.wav')
            print(f"[DEBUG] Checking set: {txt_path}, {agent_audio_path}, {emp_audio_path}")
            if not (os.path.exists(agent_audio_path) and os.path.exists(emp_audio_path)):
                print(f"[DEBUG] Skipping incomplete set: {base}")
                continue  # skip incomplete sets
            # Extract call_id from filename
            import re
            match = re.search(r"(\d{6})", base)
            call_id_val = match.group(1) if match else None
            # Avoid duplicate import: check if already in DB by call_id
            if call_id_val and calls_collection.find_one({"call_id": call_id_val}):
                print(f"[DEBUG] Skipping duplicate set by call_id: {call_id_val}")
                continue
            # ...existing code for processing and inserting...
            with open(txt_path, 'r', encoding='utf-8') as f:
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
            def get_audio_duration(filepath):
                try:
                    audio = AudioSegment.from_file(filepath)
                    return round(len(audio) / 1000, 2)  # duration in seconds
                except Exception:
                    return None
            duration = get_audio_duration(agent_audio_path)
            department = None
            topic = None
            if analysis.get('call_summary'):
                department = analysis['call_summary'].get('department')
                topic = analysis['call_summary'].get('topic')
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
            agent_name = extract_agent_name(transcript)
            dash_chars = r"[-–—‒−]"
            timestamps = []
            for line in transcript.splitlines():
                match = re.match(r"(\d{2}:\d{2}:\d{2})\s*" + dash_chars, line)
                if match:
                    timestamps.append(match.group(1))
            call_start = timestamps[0] if timestamps else datetime.utcnow().strftime('%H:%M:%S')
            call_end = timestamps[-1] if len(timestamps) > 1 else None
            doc = {
                "agent_audio": os.path.basename(agent_audio_path),
                "employee_audio": os.path.basename(emp_audio_path),
                "transcript": transcript,
                "analysis": analysis,
                "segment_times_agent": segment_times_agent,
                "segment_zcrs_agent": segment_zcrs_agent,
                "segment_times_employee": segment_times_employee,
                "segment_zcrs_employee": segment_zcrs_employee,
                "avg_speech_rate": avg_speech_rate_agent,
                "calm_score": calm_score,
                "vad_times": vad_times,
                "valence_list": valence_list,
                "arousal_list": arousal_list,
                "dominance_list": dominance_list,
                "agent_sentiment_percent": agent_sentiment_percent,
                "employee_sentiment_percent": employee_sentiment_percent,
                "sentiment_flow": sentiment_flow,
                "overall_score": overall_score,
                "duration": duration,
                "department": department,
                "topic": topic,
                "agent_name": agent_name,
                "call_id": call_id_val,  # <-- Ensure call_id is included
                "user_name": "ScheduledSync",
                "user_email": "",
                "created_at": datetime.utcnow(),
                "call_start": call_start,
                "call_end": call_end
            }
            try:
                calls_collection.insert_one(doc)
                processed += 1
            except Exception as e:
                if 'duplicate key error' in str(e):
                    print(f"[DEDUP] Duplicate call_id {doc.get('call_id')} not inserted.")
                else:
                    print(f"[ERROR] Exception inserting doc: {e}")
                    errors = []  # Ensure errors is defined
                    errors.append(f"Error inserting doc: {str(e)}")
        except Exception as e:
            print(f"[ERROR] Exception processing {txt_path}: {e}")
            errors.append(f"Error processing {txt_path}: {str(e)}")
    if errors:
        print(f"[DEBUG] Errors encountered: {errors}")
        return jsonify({"status": "error", "processed": processed, "errors": errors}), 500
    print(f"[DEBUG] Local sync complete. {processed} new calls imported.")
    return jsonify({"status": "success", "processed": processed}), 200

def perform_local_sync():
    local_dir = os.path.join(os.path.dirname(__file__), 'local_sync')
    txt_files = glob.glob(os.path.join(local_dir, '*.txt'))
    print(f"[SCHEDULER] Scanning folder: {local_dir}")
    print(f"[SCHEDULER] Found transcript files: {txt_files}")
    processed = 0
    errors = []
    for txt_path in txt_files:
        try:
            base = os.path.splitext(os.path.basename(txt_path))[0]
            agent_audio_path = os.path.join(local_dir, f'{base}_agent.wav')
            emp_audio_path = os.path.join(local_dir, f'{base}_emp.wav')
            print(f"[SCHEDULER] Checking set: {txt_path}, {agent_audio_path}, {emp_audio_path}")
            if not (os.path.exists(agent_audio_path) and os.path.exists(emp_audio_path)):
                print(f"[SCHEDULER] Skipping incomplete set: {base}")
                continue
            # Extract call_id from filename
            import re
            match = re.search(r"(\d{6})", base)
            call_id_val = match.group(1) if match else None
            # Avoid duplicate import: check if already in DB by call_id
            if call_id_val and calls_collection.find_one({"call_id": call_id_val}):
                print(f"[SCHEDULER] Skipping duplicate set by call_id: {call_id_val}")
                continue
            print(f"[SCHEDULER] Processing set: {base}")
            with open(txt_path, 'r', encoding='utf-8') as f:
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
            def get_audio_duration(filepath):
                try:
                    audio = AudioSegment.from_file(filepath)
                    return round(len(audio) / 1000, 2)  # duration in seconds
                except Exception:
                    return None
            duration = get_audio_duration(agent_audio_path)
            department = None
            topic = None
            if analysis.get('call_summary'):
                department = analysis['call_summary'].get('department')
                topic = analysis['call_summary'].get('topic')
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
            agent_name = extract_agent_name(transcript)
            dash_chars = r"[-–—‒−]"
            timestamps = []
            for line in transcript.splitlines():
                match = re.match(r"(\d{2}:\d{2}:\d{2})\s*" + dash_chars, line)
                if match:
                    timestamps.append(match.group(1))
            call_start = timestamps[0] if timestamps else datetime.utcnow().strftime('%H:%M:%S')
            call_end = timestamps[-1] if len(timestamps) > 1 else None
            doc = {
                "agent_audio": os.path.basename(agent_audio_path),
                "employee_audio": os.path.basename(emp_audio_path),
                "transcript": transcript,
                "analysis": analysis,
                "segment_times_agent": segment_times_agent,
                "segment_zcrs_agent": segment_zcrs_agent,
                "segment_times_employee": segment_times_employee,
                "segment_zcrs_employee": segment_zcrs_employee,
                "avg_speech_rate": avg_speech_rate_agent,
                "calm_score": calm_score,
                "vad_times": vad_times,
                "valence_list": valence_list,
                "arousal_list": arousal_list,
                "dominance_list": dominance_list,
                "agent_sentiment_percent": agent_sentiment_percent,
                "employee_sentiment_percent": employee_sentiment_percent,
                "sentiment_flow": sentiment_flow,
                "overall_score": overall_score,
                "duration": duration,
                "department": department,
                "topic": topic,
                "agent_name": agent_name,
                "call_id": call_id_val,  # <-- Ensure call_id is included
                "user_name": "ScheduledSync",
                "user_email": "",
                "created_at": datetime.utcnow(),
                "call_start": call_start,
                "call_end": call_end
            }
            try:
                calls_collection.insert_one(doc)
                processed += 1
            except Exception as e:
                if 'duplicate key error' in str(e):
                    print(f"[DEDUP] Duplicate call_id {doc.get('call_id')} not inserted.")
                else:
                    print(f"[SCHEDULER][ERROR] Exception inserting doc: {e}")
                    errors.append(f"Error inserting doc: {str(e)}")
        except Exception as e:
            print(f"[SCHEDULER][ERROR] Exception processing {txt_path}: {e}")
            errors.append(f"Error processing {txt_path}: {str(e)}")
    if errors:
        print(f"[SCHEDULER] Errors encountered: {errors}")
    print(f"[SCHEDULER] Local sync complete. {processed} new calls imported.")

# --- MongoDB Setup ---
MONGO_URI = ("mongodb+srv://lokesh:lokesh17@cluster0.au3rwov.mongodb.net/"
             "?retryWrites=true&w=majority")
client = MongoClient(
    MONGO_URI,
    tls=True,                  # explicit but optional; SRV implies TLS
    tlsCAFile=certifi.where()  # <— give OpenSSL an up‑to‑date CA bundle
)
db = client["post_call"]
calls_collection = db["calls"]

# Ensure unique index on call_id for hard deduplication
try:
    calls_collection.create_index("call_id", unique=True)
    print("[MongoDB] Ensured unique index on call_id.")
except Exception as e:
    print(f"[MongoDB] Error creating unique index on call_id: {e}")

# --- APScheduler Setup ---
scheduler = BackgroundScheduler()
# Track current sync interval (default 1 min)
current_sync_interval = {'minutes': 1}

def get_sync_job_status():
    try:
        job = scheduler.get_job('local_sync_job')
        if job:
            # Robustly extract interval from trigger
            trigger = job.trigger
            interval_minutes = None
            try:
                # APScheduler IntervalTrigger
                if hasattr(trigger, 'interval'):
                    total_seconds = trigger.interval.total_seconds()
                    if total_seconds % 60 == 0:
                        interval_minutes = int(total_seconds // 60)
                    else:
                        interval_minutes = round(total_seconds / 60, 2)
                else:
                    print(f"[SCHEDULER][WARN] Trigger does not have 'interval': {trigger}")
            except Exception as e:
                print(f"[SCHEDULER][ERROR] Could not extract interval: {e}")
            # Safely handle next_run_time
            next_run = job.next_run_time if hasattr(job, 'next_run_time') else None
            if next_run is not None:
                next_run_str = str(next_run)
            else:
                next_run_str = "Not scheduled"
            return {
                'running': True,
                'next_run_time': next_run_str,
                'interval': interval_minutes if interval_minutes is not None else current_sync_interval['minutes']
            }
        else:
            return {'running': False, 'next_run_time': None, 'interval': current_sync_interval['minutes']}
    except Exception as e:
        print(f"[SCHEDULER][ERROR] Exception in get_sync_job_status: {e}")
        return {'running': False, 'next_run_time': None, 'interval': current_sync_interval['minutes']}

@app.route('/get_sync_status', methods=['GET'])
@login_required
def get_sync_status():
    status = get_sync_job_status()
    return jsonify(status)

@app.route('/set_sync_interval', methods=['POST'])
@login_required
def set_sync_interval():
    data = request.get_json()
    minutes = int(data.get('minutes', 1))
    from apscheduler.jobstores.base import JobLookupError
    try:
        scheduler.remove_job('local_sync_job')
    except JobLookupError:
        # Job does not exist, this is fine
        pass
    except Exception as e:
        print(f"[SCHEDULER][ERROR] Could not remove job: {e}")
    try:
        scheduler.add_job(perform_local_sync, 'interval', minutes=minutes, id='local_sync_job', replace_existing=True)
        current_sync_interval['minutes'] = minutes
        return jsonify({'status': 'success', 'interval': minutes})
    except Exception as e:
        print(f"[SCHEDULER][ERROR] Could not add job: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

# Only add the job and start scheduler in main process (not reloader)
if __name__ == '__main__':
    print("[SCHEDULER] Starting scheduler.")
    if not scheduler.get_job('local_sync_job'):
        scheduler.add_job(perform_local_sync, 'interval', minutes=current_sync_interval['minutes'], id='local_sync_job', replace_existing=True)
    scheduler.start()
    app.run(debug=True, use_reloader=False)

@app.route('/list_call_ids', methods=['GET'])
@login_required
def list_call_ids():
    # List all call_id values in the MongoDB calls collection
    call_ids = list(calls_collection.distinct('call_id'))
    return jsonify({'call_ids': call_ids, 'count': len(call_ids)})