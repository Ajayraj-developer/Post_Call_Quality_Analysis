from flask import Flask, request, render_template, jsonify, session, redirect, url_for
import os
from dotenv import load_dotenv
import msal
import requests
import json
import tempfile
from analytics import PROMPTS, generate_section
from emotion_inference import calculate_average_speech_rate, get_calm_score, get_vad_over_time
from context_based import get_agent_employee_sentiment, parse_transcript_lines, get_sentiment_flow
from functools import wraps

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'pxJ8Q~BxiUUQXC0ngGcm8FWIntjMvFRPMhQOWcrG')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Create a directory for token cache files
TOKEN_CACHE_DIR = 'token_cache'
os.makedirs(TOKEN_CACHE_DIR, exist_ok=True)

# Microsoft Authentication Configuration
CLIENT_ID = "9297e893-98da-423c-8c1f-24c626c6c47a"
CLIENT_SECRET = os.environ.get("CLIENT_SECRET")
AUTHORITY = "https://login.microsoftonline.com/f5791d91-daca-4d28-8700-680f7a2f8b6a"
REDIRECT_PATH = "/getAToken"
SCOPE = ["User.ReadBasic.All"]
REDIRECT_URI = "http://localhost:5000/getAToken"

# Add validation to ensure CLIENT_SECRET is set
if not CLIENT_SECRET:
    raise ValueError("CLIENT_SECRET environment variable is not set. Please set it before running the application.")

def _get_cache_file_path():
    """Get the path for the token cache file based on session ID"""
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
            # If cache file is corrupted, ignore it
            pass
    return cache

def _save_cache(cache):
    if cache.has_state_changed:
        cache_file = _get_cache_file_path()
        try:
            with open(cache_file, 'w') as f:
                f.write(cache.serialize())
        except IOError:
            # If we can't save the cache, continue anyway
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
    """Get the path for the flow cache file based on session ID"""
    session_id = session.get('session_id')
    if not session_id:
        import uuid
        session_id = str(uuid.uuid4())
        session['session_id'] = session_id
    return os.path.join(TOKEN_CACHE_DIR, f'flow_{session_id}.json')

def _save_flow(flow):
    """Save the OAuth flow to a file"""
    flow_file = _get_flow_file_path()
    try:
        with open(flow_file, 'w') as f:
            json.dump(flow, f)
    except IOError:
        pass

def _load_flow():
    """Load the OAuth flow from a file"""
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
    # Create the full OAuth flow and save it to file
    flow = _build_auth_code_flow(scopes=SCOPE)
    _save_flow(flow)
    return render_template("login.html", auth_url=flow["auth_uri"])

@app.route(REDIRECT_PATH)
def authorized():
    try:
        cache = _load_cache()
        
        # Load the complete flow from file
        flow = _load_flow()
        if not flow:
            print("No flow found in cache")
            return redirect(url_for("login"))
        
        result = _build_msal_app(cache=cache).acquire_token_by_auth_code_flow(
            flow, request.args)
        
        if "error" in result:
            print(f"Authentication error: {result}")
            return render_template("auth_error.html", result=result)
        
        # Store only essential user info in session
        user_info = result.get("id_token_claims", {})
        session["user"] = {
            "name": user_info.get("name", "Unknown"),
            "preferred_username": user_info.get("preferred_username", ""),
            "oid": user_info.get("oid", "")
        }
        
        _save_cache(cache)
        
        # Clean up flow file
        try:
            flow_file = _get_flow_file_path()
            if os.path.exists(flow_file):
                os.remove(flow_file)
        except Exception:
            pass
        
    except Exception as e:
        print(f"Authorization error: {e}")
        return redirect(url_for("login"))
    
    return redirect(url_for("upload_audio"))

@app.route("/logout")
def logout():
    # Clean up token cache file and flow file
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
        "?post_logout_redirect_uri=" + url_for("login", _external=True))

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
            user=session.get("user"),
        )

    return render_template('upload.html', user=session.get("user"))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)