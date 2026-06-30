from flask import Flask, request, render_template, jsonify, session, redirect, url_for
from send_mail_util import send_email
import msal
import json
from functools import wraps
import os
from analytics import PROMPTS, generate_section
from emotion_inference import calculate_average_speech_rate, get_calm_score, get_vad_over_time
from context_based import get_agent_employee_sentiment, parse_transcript_lines, get_sentiment_flow
from data import CallDataRepository, get_db_url, extract_agent_name

from dotenv import load_dotenv
import uuid
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler

# Load .env from project root
dotenv_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(dotenv_path)

# Microsoft Authentication Configuration (defaults kept from original project)
CLIENT_ID = os.environ.get('CLIENT_ID', '9297e893-98da-423c-8c1f-24c626c6c47a')
CLIENT_SECRET = os.environ.get('CLIENT_SECRET')
AUTHORITY = os.environ.get('AUTHORITY', 'https://login.microsoftonline.com/f5791d91-daca-4d28-8700-680f7a2f8b6a')
REDIRECT_PATH = os.environ.get('REDIRECT_PATH', '/getAToken')
SCOPE = json.loads(os.environ.get('SCOPE_JSON', '[]')) if os.environ.get('SCOPE_JSON') else ["User.ReadBasic.All"]
REDIRECT_URI = os.environ.get('REDIRECT_URI', 'http://localhost:5000/getAToken')

# Flask app
app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'pxJ8Q~BxiUUQXC0ngGcm8FWIntjMvFRPMhQOWcrG')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

TOKEN_CACHE_DIR = os.path.join(os.path.dirname(__file__), 'token_cache')
os.makedirs(TOKEN_CACHE_DIR, exist_ok=True)

# Scheduler for background jobs
scheduler = BackgroundScheduler()

def _get_cache_file_path():
    session_id = session.get('session_id')
    if not session_id:
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
        if not session.get('user'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def _get_flow_file_path():
    session_id = session.get('session_id')
    if not session_id:
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
    return redirect(url_for("dashboard"))

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
        "?post_logout_redirect_uri=" + url_for("login", _external=True, _scheme="https", _server="127.0.0.1:5000"))

@app.route('/upload', methods=['GET', 'POST'])
@login_required
def upload_audio():

    if request.method == 'POST':
        # ...existing POST logic...
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
        # Save all analysis and metadata to MySQL
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
        # Load DB credentials and initialize repository
        db_url = get_db_url()
        repo = CallDataRepository(db_url)
        try:
            repo.insert_call(doc)
            # Use the 6-digit business call_id for redirect, not the SQL row id
            return redirect(url_for('view_call', call_id=doc["call_id"]))
        except Exception as e:
            error_str = str(e).lower()
            if 'duplicate entry' in error_str or 'violation of unique key constraint' in error_str:
                print(f"[DEDUP] Duplicate call_id {doc.get('call_id')} not inserted.")
                return "Duplicate call_id not inserted.", 409
            else:
                print(f"[ERROR] Exception inserting doc: {e}")
                return f"Error inserting document into database: {str(e)}", 500

    # Fetch call list for display
    repo = CallDataRepository(get_db_url())
    call_records = repo.get_all_calls(limit=20, offset=0)
    call_list = []
    for call in call_records:
        transcript = call.get("transcript", "")
        agent_name = call.get("agent_name") or extract_agent_name(transcript)
        call_summary = (call.get("analysis") or {}).get("call_summary", {}) if call.get("analysis") else {}
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
        call_id = call.get("call_id", "N/A")
        call_list.append({
            "_id": str(call.get("id")),
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
        })
    return render_template('upload.html', user=session.get("user"), call_list=call_list)
# Set dashboard as the landing page
@app.route('/')
@login_required
def landing_dashboard():
    return dashboard()
# --- Notification Email Route ---
@app.route('/send_notification_mail', methods=['POST'])
@login_required
def send_notification_mail():
    manager_email = "ravi.v72@lwpcoe.com"
    subject = "Sample Notification"
    body = (
        "Hello Manager,\n\n"
        "This is a sample email sent from the dashboard notification button.\n\n"
        "Best regards,\nCOE App System"
    )
    email_sent = send_email(manager_email, subject, body)
    if email_sent:
        return jsonify({'message': 'Sample email sent successfully!'}), 200
    else:
        return jsonify({'error': 'Failed to send sample email.'}), 500
import msal

@app.route('/dashboard')
@login_required
def dashboard():
    # Fetch call list using CallDataRepository
    repo = CallDataRepository(get_db_url())
    # Filtering logic
    from datetime import datetime, timedelta
    filter_type = request.args.get('filter', 'total')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    # Pagination (optional, can be extended)
    page = int(request.args.get('page', 1))
    per_page = 10
    offset = (page - 1) * per_page
    all_calls = repo.get_all_calls()
    filtered_calls = []
    today = datetime.utcnow().date()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    def parse_date(dt):
        if isinstance(dt, datetime):
            return dt.date()
        try:
            return datetime.strptime(str(dt)[:10], '%Y-%m-%d').date()
        except:
            return None
    for call in all_calls:
        call_date = parse_date(call.get('created_at'))
        if filter_type == 'today':
            if call_date == today:
                filtered_calls.append(call)
        elif filter_type == 'wtd':
            if call_date and week_start <= call_date <= today:
                filtered_calls.append(call)
        elif filter_type == 'mtd':
            if call_date and month_start <= call_date <= today:
                filtered_calls.append(call)
        elif filter_type == 'custom' and start_date and end_date:
            try:
                sd = datetime.strptime(start_date, '%Y-%m-%d').date()
                ed = datetime.strptime(end_date, '%Y-%m-%d').date()
                if call_date and sd <= call_date <= ed:
                    filtered_calls.append(call)
            except:
                pass
        else:  # total or fallback
            filtered_calls.append(call)
    # Pagination on filtered_calls
    call_records = filtered_calls[offset:offset+per_page]

    call_list = []
    agent_stats = {}
    for call in call_records:
        transcript = call.get("transcript", "")
        agent_name = call.get("agent_name")
        if not agent_name or agent_name.lower() in ["", "unknown", "agent"]:
            agent_name = extract_agent_name(transcript)
        if not agent_name or agent_name.lower() in ["", "unknown", "agent"]:
            agent_name = "Unknown"
        # Parse analysis/call_summary if present
        call_summary = (call.get("analysis") or {}).get("call_summary", {}) if call.get("analysis") else {}
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
        call_id = call.get("call_id", "N/A")
        # Use sop_steps_followed from doc if present, else fallback to calculation from analysis
        sop_steps_followed = call.get('sop_steps_followed')
        if sop_steps_followed is None and call.get("analysis"):
            sop = (call.get("analysis") or {}).get("sop_adherence", {})
            sop_steps = [
                sop.get('identity_verification'),
                sop.get('security_questions'),
                sop.get('two_factor_enabled'),
                sop.get('account_activity_review'),
                sop.get('security_best_practices')
            ]
            sop_steps_followed = sum(1 for s in sop_steps if s)
        # Aggregate stats for Top 5 Engineers
        if agent_name not in agent_stats:
            agent_stats[agent_name] = {
                'overall': 0, 'sop': 0, 'voice': 0, 'tech': 0, 'speech': 0, 'count': 0
            }
        # Only count numeric values
        overall_score = call.get('overall_score')
        sop_val = sop_steps_followed
        voice_val = call.get('voice_elevation_freq', 0)
        tech_val = len(call.get('valence_list', [])) if call.get('valence_list') else 0
        speech_val = call.get('avg_speech_rate', 0)
        agent_stats[agent_name]['overall'] += overall_score if isinstance(overall_score, (int, float)) else 0
        agent_stats[agent_name]['sop'] += sop_val if isinstance(sop_val, (int, float)) else 0
        agent_stats[agent_name]['voice'] += voice_val if isinstance(voice_val, (int, float)) else 0
        agent_stats[agent_name]['tech'] += tech_val if isinstance(tech_val, (int, float)) else 0
        agent_stats[agent_name]['speech'] += speech_val if isinstance(speech_val, (int, float)) else 0
        agent_stats[agent_name]['count'] += 1
        call_list.append({
            "_id": str(call.get("id")),
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
            "sop_steps_followed": sop_steps_followed,
            "employee_sentiment_percent": call.get("employee_sentiment_percent", None)
        })
    # Prepare Top 5 Engineers data (skip agents with count==0)
    valid_agents = [(name, stats) for name, stats in agent_stats.items() if stats['count'] > 0 and name.lower() not in ["", "unknown", "agent"]]
    sorted_agents = sorted(valid_agents, key=lambda x: (x[1]['overall']/x[1]['count'] if x[1]['count'] else 0), reverse=True)
    top5 = []
    for i in range(5):
        if i < len(sorted_agents):
            name, stats = sorted_agents[i]
            count = stats['count'] if stats['count'] else 1
            top5.append({
                'name': name,
                'overall': round(stats['overall']/count, 2),
                'sop': round(stats['sop']/count*20, 2),
                'voice': round(stats['voice']/count, 2),
                'tech': round(stats['tech']/count, 2),
                'speech': round(stats['speech']/count, 2)
            })
        else:
            # Static fallback
            static_names = ['John Smith', 'Sarah Johnson', 'Mike Thompson', 'Emily Davis', 'Robert Williams']
            static_overall = [92, 89, 85, 94, 87]
            static_sop = [88, 92, 79, 96, 85]
            static_voice = [15, 12, 25, 8, 18]
            static_tech = [95, 87, 83, 91, 89]
            static_speech = [89, 94, 81, 97, 86]
            top5.append({
                'name': static_names[i],
                'overall': static_overall[i],
                'sop': static_sop[i],
                'voice': static_voice[i],
                'tech': static_tech[i],
                'speech': static_speech[i]
            })
    # Ensure we pass a user dict with both name and email (preferred_username) for templates
    sess_user = session.get("user") or {}
    user_ctx = {
        "name": sess_user.get("name", "Guest User"),
        # prefer preferred_username if present, else try email key
        "email": sess_user.get("preferred_username") or sess_user.get("email") or ''
    }
    return render_template('dashboard.html', user=user_ctx, call_list=call_list, top5_engineers=top5)
@app.route('/team')
def team():
    return render_template('team.html')
@app.route('/faq')
@login_required
def faq():
    # Ensure we pass a user dict with both name and email (preferred_username) for templates
    sess_user = session.get("user") or {}
    user_ctx = {
        "name": sess_user.get("name", "Guest User"),
        # prefer preferred_username if present, else try email key
        "email": sess_user.get("preferred_username") or sess_user.get("email") or ''
    }
    return render_template('faq.html', user=user_ctx)

@app.route('/real_time_operations')
@login_required
def real_time_operations():
    # Render the real-time operations page
    return render_template('real_time_operations.html', user=session.get('user'))


@app.route('/agent_performance')
@login_required
def agent_performance():
    return render_template('agent_performance.html', user=session.get('user'))


@app.route('/sentiment_analytics')
@login_required
def sentiment_analytics():
    return render_template('sentiment_analytics.html', user=session.get('user'))


@app.route('/executive_overview')
@login_required
def executive_overview():
    return render_template('executive_overview.html', user=session.get('user'))
@app.route('/sync_cloud', methods=['POST'])
@login_required
def sync_cloud():
    """Sync cloud calls by reading blob URLs from a DB table and processing them like local sync."""
    download_dir = os.path.join(os.path.dirname(__file__), 'Cloud_sync', 'downloaded_transcripts')
    os.makedirs(download_dir, exist_ok=True)

    table_name = os.environ.get('CLOUD_SYNC_TABLE', 'call_data11')
    repo = CallDataRepository(get_db_url())
    processed, errors, last_call_id = repo.sync_cloud_from_db(
        table_name=table_name,
        download_dir=download_dir,
        user_name=session.get('user', {}).get('name', 'Unknown'),
        user_email=session.get('user', {}).get('preferred_username', ''),
        verbose=True
    )

    if errors:
        print(f"[CLOUD SYNC] Errors encountered: {errors}")
        return jsonify({"status": "error", "processed": processed, "errors": errors}), 500

    print(f"[CLOUD SYNC] Cloud sync complete. {processed} new calls imported.")
    return jsonify({"status": "success", "processed": processed, "last_call_id": last_call_id}), 200

@app.route('/call/<call_id>')
@login_required
def view_call(call_id):
    # Fetch call list using CallDataRepository
    repo = CallDataRepository(get_db_url())
    call = repo.get_call_by_call_id(call_id)
    if not call:
        return "Call not found", 404
    # Recompute sentiment values from transcript when the stored values are missing or zero.
    transcript = call.get('transcript')
    if transcript:
        parsed_transcript = parse_transcript_lines(transcript)
        needs_sentiment_recompute = (
            not call.get('agent_sentiment_percent')
            or not call.get('employee_sentiment_percent')
            or not call.get('sentiment_flow')
            or call.get('agent_sentiment_percent') == 0
            or call.get('employee_sentiment_percent') == 0
        )
        if parsed_transcript and needs_sentiment_recompute:
            sentiment_scores = get_agent_employee_sentiment(parsed_transcript)
            call['agent_sentiment_percent'] = sentiment_scores['agent_sentiment_percent']
            call['employee_sentiment_percent'] = sentiment_scores['employee_sentiment_percent']
            call['sentiment_flow'] = get_sentiment_flow(parsed_transcript)

    # Format duration: show seconds if <60, else show minutes
    raw_duration = call.get('duration')
    if raw_duration is not None:
        try:
            dur_float = float(raw_duration)
            if dur_float < 60:
                duration_str = f"{int(dur_float)} sec"
            else:
                duration_str = f"{round(dur_float/60, 2)} min"
        except Exception:
            duration_str = str(raw_duration)
    else:
        duration_str = 'N/A'
    return render_template(
        'analytics.html',
        duration=duration_str,
        call_id=call.get('call_id'),
        agent_name=call.get('agent_name'),
        created_at=call.get('created_at'),
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
    repo = CallDataRepository(get_db_url())
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
            # Avoid duplicate import: check if already in MySQL by call_id
            if call_id_val:
                existing = repo.get_call_by_call_id(call_id_val)
                if existing:
                    print(f"[DEBUG] Skipping duplicate set by call_id: {call_id_val}")
                    continue
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
            # Calculate duration (in seconds) for agent audio
            def get_audio_duration(filepath):
                try:
                    audio = AudioSegment.from_file(filepath)
                    return round(len(audio) / 1000, 2)
                except Exception:
                    return None
            duration = get_audio_duration(agent_audio_path)
            # Extract department and topic from analysis if available
            department = None
            topic = None
            if analysis.get('call_summary'):
                department = analysis['call_summary'].get('department')
                topic = analysis['call_summary'].get('topic')
            # Extract agent name from transcript
            agent_name = extract_agent_name(transcript)
            # Extract timestamps from transcript
            dash_chars = r"[-–—‒−]"
            timestamps = []
            for line in transcript.splitlines():
                match = re.match(r"(\d{2}:\d{2}:\d{2})\s*" + dash_chars, line)
                if match:
                    timestamps.append(match.group(1))
            call_start = timestamps[0] if timestamps else datetime.utcnow().strftime('%H:%M:%S')
            call_end = timestamps[-1] if len(timestamps) > 1 else None
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
            try:
                created_at_val = datetime.utcfromtimestamp(os.path.getmtime(txt_path))
            except Exception:
                created_at_val = datetime.utcnow()
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
                "overall_score": 0.4 * context_score + 0.3 * tone_score + 1.0 * sop_score + 1.0 * agent_score + 1.0 * resolved_score,
                "duration": duration,
                "department": department,
                "topic": topic,
                "agent_name": agent_name,
                "call_id": call_id_val,
                "user_name": session.get("user", {}).get("name", "Unknown"),
                "user_email": session.get("user", {}).get("preferred_username", ""),
                "created_at": created_at_val,
                "call_start": call_start,
                "call_end": call_end,
                "voice_elevation_freq": voice_elevation_freq,
                "sop_steps_followed": sop_steps_followed
            }
            try:
                repo.insert_call(doc)
                processed += 1
            except Exception as e:
                error_str = str(e).lower()
                if 'duplicate entry' in error_str or 'violation of unique key constraint' in error_str:
                    print(f"[DEDUP] Duplicate call_id {doc.get('call_id')} not inserted.")
                else:
                    print(f"[ERROR] Exception inserting doc: {e}")
                    errors.append(f"Error inserting doc: {str(e)}")
        except Exception as e:
            print(f"[SYNC_LOCAL][ERROR] Exception processing {txt_path}: {e}")
            errors.append(f"Error processing {txt_path}: {str(e)}")
    if errors:
        print(f"[SYNC_LOCAL] Errors encountered: {errors}")
        return jsonify({"status": "error", "processed": processed, "errors": errors}), 500
    print(f"[SYNC_LOCAL] Local sync complete. {processed} new calls imported.")
    return jsonify({"status": "success", "processed": processed}), 200





# --- Scheduled Sync Support (Local + Cloud) ---
current_sync_interval = {'minutes': 5}

def perform_local_sync():
    """Background job to sync local files using CallDataRepository."""
    repo = CallDataRepository(get_db_url())
    local_dir = os.path.join(os.path.dirname(__file__), 'local_sync')
    processed, errors = repo.sync_local_folder(local_dir, user_name=None, user_email=None, verbose=True)
    if errors:
        print(f"[SCHEDULER] Local sync errors: {errors}")
    print(f"[SCHEDULER] Local sync complete. {processed} new calls imported.")

def perform_cloud_sync():
    """Background job to sync cloud calls from the external DB table automatically."""
    try:
        download_dir = os.path.join(os.path.dirname(__file__), 'Cloud_sync', 'downloaded_transcripts')
        os.makedirs(download_dir, exist_ok=True)
        table_name = os.environ.get('CLOUD_SYNC_TABLE', 'call_data11')
        repo = CallDataRepository(get_db_url())
        processed, errors, last_call_id = repo.sync_cloud_from_db(
            table_name=table_name,
            download_dir=download_dir,
            user_name='AutoSync',
            user_email='',
            verbose=True
        )
        if errors:
            print(f"[SCHEDULER] Cloud sync errors: {errors}")
        print(f"[SCHEDULER] Cloud sync complete. {processed} new calls imported.")
    except Exception as e:
        print(f"[SCHEDULER] Cloud sync exception: {e}")

def perform_combined_sync():
    """Background job that runs both local and cloud sync."""
    perform_local_sync()
    perform_cloud_sync()

def get_sync_job_status():
    job = scheduler.get_job('local_sync_job')
    interval_minutes = None
    try:
        if job:
            trigger = job.trigger
            if hasattr(trigger, 'interval'):
                total_seconds = trigger.interval.total_seconds()
                if total_seconds % 60 == 0:
                    interval_minutes = int(total_seconds // 60)
                else:
                    interval_minutes = round(total_seconds / 60, 2)
            next_run = job.next_run_time if hasattr(job, 'next_run_time') else None
            next_run_str = str(next_run) if next_run else "Not scheduled"
            return {
                'running': True,
                'next_run_time': next_run_str,
                'interval': interval_minutes if interval_minutes is not None else current_sync_interval['minutes']
            }
    except Exception as e:
        print(f"[SCHEDULER][ERROR] Exception in get_sync_job_status: {e}")
    return {'running': False, 'next_run_time': None, 'interval': current_sync_interval['minutes']}


# --- Ensure scheduler and job are always started ---
def ensure_scheduler_job():
    if not scheduler.get_job('local_sync_job'):
        print("[SCHEDULER] Adding combined sync job (local + cloud).")
        scheduler.add_job(perform_combined_sync, 'interval', minutes=current_sync_interval['minutes'], id='local_sync_job', replace_existing=True)
    if not scheduler.running:
        print("[SCHEDULER] Starting scheduler.")
        scheduler.start()

ensure_scheduler_job()


# --- Flask routes for sync status and interval ---
@app.route('/get_sync_status', methods=['GET'])
@login_required
def get_sync_status():
    status = get_sync_job_status()
    return jsonify(status)

@app.route('/set_sync_interval', methods=['POST'])
@login_required
def set_sync_interval():
    # Accept both JSON and form data
    data = request.get_json(silent=True) or request.form or {}
    try:
        minutes = int(data.get('minutes', 5))
        if minutes < 1:
            return jsonify({'status': 'error', 'message': 'Interval must be >= 1 minute'}), 400
    except Exception:
        return jsonify({'status': 'error', 'message': 'Invalid minutes value'}), 400
    from apscheduler.jobstores.base import JobLookupError
    try:
        scheduler.remove_job('local_sync_job')
    except JobLookupError:
        pass
    except Exception as e:
        print(f"[SCHEDULER][ERROR] Could not remove job: {e}")
    try:
        scheduler.add_job(perform_combined_sync, 'interval', minutes=minutes, id='local_sync_job', replace_existing=True)
        current_sync_interval['minutes'] = minutes
        print(f"[SCHEDULER] Updated sync job interval to {minutes} minutes (local + cloud).")
        return jsonify({'status': 'success', 'interval': minutes})
    except Exception as e:
        print(f"[SCHEDULER][ERROR] Could not add job: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

# --- API endpoint for dashboard auto-refresh polling ---
@app.route('/api/call_count', methods=['GET'])
@login_required
def api_call_count():
    """Returns the current total call count so the dashboard can detect new calls."""
    repo = CallDataRepository(get_db_url())
    all_calls = repo.get_all_calls()
    return jsonify({'count': len(all_calls)})

# Only run Flask app if main
if __name__ == '__main__':
    app.run(debug=True, use_reloader=False)
