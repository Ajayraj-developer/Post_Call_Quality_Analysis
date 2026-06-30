from sqlalchemy import create_engine, Column, Integer, String, Float, Text, DateTime, JSON, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import datetime
import os
from urllib.parse import quote_plus
import re

def extract_agent_name(transcript):
    """
    Robust extraction of agent/support name from transcript lines.
    Supports formats like:
      - 00:00:05 - Agent (Raghav): hello
      - 00:00:05 - Agent1: hello
      - 00:00:05 - support: Hello, my name is Alex.
    """
    if not transcript:
        return "Unknown"
    try:
        lines = transcript.splitlines()
        for line in lines:
            # Match "timestamp - Speaker: text"
            match = re.match(r'^\s*\d{1,2}:\d{2}:\d{2}\s*[–-]\s*([^:]+):', line)
            if match:
                speaker = match.group(1).strip()
                if speaker.lower() != 'customer':
                    # Extract name from parentheses if present (e.g. Agent (Raghav))
                    paren_match = re.search(r'\(([^)]+)\)', speaker)
                    if paren_match:
                        return paren_match.group(1).strip()
                    
                    # If matches Agent\d+ or support\d+
                    if re.match(r'^(agent|support)\d+$', speaker, re.IGNORECASE):
                        return speaker
                    
                    # Try finding "my name is X" or "this is X" in line
                    text_part = line[match.end():].strip()
                    name_match = re.search(r'\bmy\s+name\s+is\s+([a-zA-Z]+)\b', text_part, re.IGNORECASE)
                    if name_match:
                        return name_match.group(1).capitalize()
                    
                    name_match2 = re.search(r'\bthis\s+is\s+([a-zA-Z]+)\b', text_part, re.IGNORECASE)
                    if name_match2:
                        possible_name = name_match2.group(1).capitalize()
                        if possible_name.lower() not in ['a', 'the', 'not', 'just', 'an', 'support', 'agent']:
                            return possible_name
                    
                    # If speaker label is not generic, use it
                    if speaker.lower() not in ['support', 'agent']:
                        return speaker
                    
                    return speaker
    except Exception:
        pass
    return "Unknown"


def extract_call_timestamp(rowdict=None, source_name=None):
    """Extract the actual call completion timestamp from MSSQL-like values or filenames."""
    candidates = []
    if isinstance(rowdict, dict):
        for key in ('date_completed', 'datecompleted', 'completed_at', 'created_at', 'timestamp', 'call_timestamp'):
            value = rowdict.get(key)
            if value:
                candidates.append(value)

    if source_name:
        candidates.append(source_name)

    for candidate in candidates:
        if not candidate:
            continue
        if isinstance(candidate, datetime.datetime):
            return candidate
        if isinstance(candidate, str):
            text = candidate.strip()
            if not text:
                continue
            patterns = [
                r'(?P<dt>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?)',
                r'(?P<dt>\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}(?:Z|))',
            ]
            for pattern in patterns:
                match = re.search(pattern, text)
                if match:
                    dt_str = match.group('dt')
                    dt_str = dt_str.replace('T', ' ')
                    dt_str = dt_str.replace('-', ':', 2) if 'T' not in candidate and dt_str.count('-') >= 2 else dt_str
                    dt_str = dt_str.replace('Z', '')
                    candidates_to_try = [dt_str]
                    if ' ' in dt_str:
                        base, rest = dt_str.split(' ', 1)
                        candidates_to_try.append(base + ' ' + rest.replace(':', '-', 2))
                    for candidate_dt in candidates_to_try:
                        try:
                            if len(candidate_dt) >= 19 and candidate_dt[10] == ' ':
                                return datetime.datetime.strptime(candidate_dt[:19], '%Y-%m-%d %H:%M:%S')
                            if len(candidate_dt) >= 19 and candidate_dt[10] == 'T':
                                return datetime.datetime.strptime(candidate_dt[:19], '%Y-%m-%dT%H:%M:%S')
                            # Handle filename-style strings like 2026-06-29T05-11-46-725Z
                            if re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}', candidate_dt):
                                return datetime.datetime.strptime(candidate_dt, '%Y-%m-%dT%H-%M-%S')
                            return datetime.datetime.fromisoformat(candidate_dt.replace('Z', '+00:00'))
                        except ValueError:
                            continue
            # Fallback for values like '2026-06-29 05:11:58.817'
            try:
                return datetime.datetime.strptime(text.split('.')[0], '%Y-%m-%d %H:%M:%S')
            except ValueError:
                pass
    return None

Base = declarative_base()

def get_db_url():
    """
    Construct database URL from environment variables.
    Supports both MySQL and MSSQL based on the DB_TYPE env variable.
    """
    db_type = os.environ.get('DB_TYPE', 'mssql').lower() # Default to mssql
    db_user = os.environ.get('DB_USER')
    db_password = os.environ.get('DB_PASSWORD')
    # Support both DB_HOST and DB_SERVER
    db_host = os.environ.get('DB_HOST') or os.environ.get('DB_SERVER', 'localhost')
    db_name = os.environ.get('DB_NAME')
    
    # Clean up Azure-style host strings (e.g., tcp:server.net,1433 -> server.net:1433)
    if db_host and 'tcp:' in db_host:
        db_host = db_host.replace('tcp:', '')
    if db_host and ',' in db_host:
        db_host = db_host.replace(',', ':')
    
    if db_type == 'mysql':
        return f"mysql+pymysql://{db_user}:{db_password}@{db_host}/{db_name}"
    else:
        # MSSQL connection string using pyodbc
        driver = os.environ.get('DB_DRIVER', 'ODBC Driver 17 for SQL Server')
        safe_password = quote_plus(db_password) if db_password else ""
        return f"mssql+pyodbc://{db_user}:{safe_password}@{db_host}/{db_name}?driver={driver.replace(' ', '+')}"

class CallData(Base):
    __tablename__ = 'call_data'
    id = Column(Integer, primary_key=True)
    call_id = Column(String(20), unique=True)
    agent_audio = Column(String(255))
    employee_audio = Column(String(255))
    transcript = Column(Text)
    analysis = Column(JSON)
    segment_times_agent = Column(JSON)
    segment_zcrs_agent = Column(JSON)
    segment_times_employee = Column(JSON)
    segment_zcrs_employee = Column(JSON)
    avg_speech_rate = Column(Float)
    calm_score = Column(Float)
    vad_times = Column(JSON)
    valence_list = Column(JSON)
    arousal_list = Column(JSON)
    dominance_list = Column(JSON)
    agent_sentiment_percent = Column(Float)
    employee_sentiment_percent = Column(Float)
    sentiment_flow = Column(JSON)
    overall_score = Column(Float)
    duration = Column(Float)
    department = Column(String(255))
    topic = Column(String(255))
    agent_name = Column(String(255))
    user_name = Column(String(255))
    user_email = Column(String(255))
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    call_start = Column(String(20))
    call_end = Column(String(20))
    voice_elevation_freq = Column(Integer)
    sop_steps_followed = Column(Integer)

class CallDataRepository:
    def __init__(self, db_url):
        self.engine = create_engine(db_url)
        # Ensure all tables exist
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def sync_local_folder(self, local_dir, user_name=None, user_email=None, verbose=True):
        """
        Scan the local_dir for transcript and audio files, deduplicate by call_id, parse, and insert new calls.
        Returns (processed_count, errors_list)
        """
        import glob
        import os
        import re
        from datetime import datetime
        from emotion_inference import calculate_average_speech_rate, get_calm_score, get_vad_over_time
        from context_based import get_agent_employee_sentiment, parse_transcript_lines, get_sentiment_flow
        from analytics import PROMPTS, generate_section
        from pydub import AudioSegment
        processed = 0
        errors = []
        txt_files = glob.glob(os.path.join(local_dir, '*.txt'))
        if verbose:
            print(f"[SYNC_LOCAL] Scanning folder: {local_dir}")
            print(f"[SYNC_LOCAL] Found transcript files: {txt_files}")
        for txt_path in txt_files:
            try:
                base = os.path.splitext(os.path.basename(txt_path))[0]
                agent_audio_path = os.path.join(local_dir, f'{base}_agent.wav')
                emp_audio_path = os.path.join(local_dir, f'{base}_emp.wav')
                if not (os.path.exists(agent_audio_path) and os.path.exists(emp_audio_path)):
                    continue
                match = re.search(r"(\d{6})", base)
                call_id_val = match.group(1) if match else None
                if call_id_val:
                    existing = self.get_call_by_call_id(call_id_val)
                    if existing:
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
                def get_audio_duration(filepath):
                    try:
                        audio = AudioSegment.from_file(filepath)
                        return round(len(audio) / 1000, 2)
                    except Exception:
                        return None
                duration = get_audio_duration(agent_audio_path)
                department = None
                topic = None
                if analysis.get('call_summary'):
                    department = analysis['call_summary'].get('department')
                    topic = analysis['call_summary'].get('topic')
                agent_name = extract_agent_name(transcript)
                dash_chars = r"[-–—‒−]"
                timestamps = []
                for line in transcript.splitlines():
                    match = re.match(r"(\d{2}:\d{2}:\d{2})\s*" + dash_chars, line)
                    if match:
                        timestamps.append(match.group(1))
                call_start = timestamps[0] if timestamps else datetime.utcnow().strftime('%H:%M:%S')
                call_end = timestamps[-1] if len(timestamps) > 1 else None
                arousal_threshold = 0.85
                voice_elevation_freq = sum(1 for a in arousal_list if a > arousal_threshold)
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
                    "user_name": user_name,
                    "user_email": user_email,
                    "created_at": created_at_val,
                    "call_start": call_start,
                    "call_end": call_end,
                    "voice_elevation_freq": voice_elevation_freq,
                    "sop_steps_followed": sop_steps_followed
                }
                try:
                    self.insert_call(doc)
                    processed += 1
                except Exception as e:
                    # Generic check for unique constraint violations across MySQL and MSSQL
                    error_str = str(e).lower()
                    if 'duplicate entry' in error_str or 'violation of unique key constraint' in error_str:
                        if verbose:
                            print(f"[DEDUP] Duplicate call_id {doc.get('call_id')} not inserted.")
                    else:
                        if verbose:
                            print(f"[ERROR] Exception inserting doc: {e}")
                        errors.append(f"Error inserting doc: {str(e)}")
            except Exception as e:
                if verbose:
                    print(f"[SYNC_LOCAL][ERROR] Exception processing {txt_path}: {e}")
                errors.append(f"Error processing {txt_path}: {str(e)}")
        if errors and verbose:
            print(f"[SYNC_LOCAL] Errors encountered: {errors}")
        if verbose:
            print(f"[SYNC_LOCAL] Local sync complete. {processed} new calls imported.")
        return processed, errors

    def sync_cloud_from_db(self, table_name='call_data11', download_dir=None, user_name=None, user_email=None, verbose=True):
        """
        Read rows from an external table (default `call_data11`) that contains
        URLs to transcript and audio files in Azure Blob Storage, download the
        files locally, process them using the same pipeline as local sync,
        and insert new calls into `call_data`.

        Returns (processed_count, errors_list)
        """
        import requests
        from urllib.parse import urlparse
        import os
        import re
        from datetime import datetime
        from emotion_inference import calculate_average_speech_rate, get_calm_score, get_vad_over_time
        from context_based import get_agent_employee_sentiment, parse_transcript_lines, get_sentiment_flow
        from analytics import PROMPTS, generate_section
        from pydub import AudioSegment

        processed = 0
        errors = []
        last_call_id = None
        session = self.Session()
        try:
            # Query the external table for rows to process
            sql = f"SELECT * FROM {table_name}"
            result = session.execute(text(sql))
            rows = result.fetchall()
            if verbose:
                print(f"[SYNC_CLOUD_DB] Found {len(rows)} rows in {table_name}")

            for row in rows:
                try:
                    # normalize access by column names if present
                    rowdict = dict(row._mapping) if hasattr(row, '_mapping') else dict(row)
                    call_id_val = str(rowdict.get('call id') or rowdict.get('call_id') or rowdict.get('callid') or rowdict.get('id'))
                    transcript_url = rowdict.get('transcript_file') or rowdict.get('transcript')
                    agent_audio_url = rowdict.get('agent_audio_file') or rowdict.get('agent_audio') or rowdict.get('agent_audio_file_url')
                    emp_audio_url = rowdict.get('emp_audio_file') or rowdict.get('employee_audio') or rowdict.get('emp_audio_file_url')
                    date_completed_val = rowdict.get('date_completed') or rowdict.get('datecompleted') or rowdict.get('completed_at')

                    if not call_id_val:
                        if verbose:
                            print('[SYNC_CLOUD_DB] skipping row without call_id')
                        continue

                    # Skip if already processed
                    if self.get_call_by_call_id(call_id_val):
                        if verbose:
                            print(f"[SYNC_CLOUD_DB] Skipping existing call_id: {call_id_val}")
                        continue

                    if not transcript_url or not agent_audio_url:
                        if verbose:
                            print(f"[SYNC_CLOUD_DB] Missing transcript or agent audio for call {call_id_val}")
                        continue

                    # prepare download folder
                    if not download_dir:
                        download_dir = os.path.join(os.path.dirname(__file__), 'Cloud_sync', 'downloaded_transcripts')
                    os.makedirs(download_dir, exist_ok=True)

                    def download(url, target_name):
                        # Attempt to download a file. If the URL is private and a global
                        # SAS token is provided in env, append it when the URL has no query.
                        from urllib.parse import urlparse, urlunparse
                        parsed = urlparse(url)
                        fname = os.path.basename(parsed.path)
                        local_path = os.path.join(download_dir, f"{call_id_val}_{target_name}_{fname}")
                        # If URL has no query and a SAS token is available in env, append it
                        try:
                            query = parsed.query
                            if not query:
                                sas_token = os.environ.get('AZURE_STORAGE_SAS_TOKEN') or os.environ.get('VITE_AZURE_STORAGE_SAS_TOKEN')
                                if sas_token:
                                    token = sas_token.lstrip('?')
                                    parsed = parsed._replace(query=token)
                                    url_to_use = urlunparse(parsed)
                                else:
                                    url_to_use = url
                            else:
                                url_to_use = url

                            r = requests.get(url_to_use, timeout=30)
                            r.raise_for_status()
                            with open(local_path, 'wb') as f:
                                f.write(r.content)
                            return local_path
                        except requests.exceptions.HTTPError as he:
                            # Return None on HTTP errors so caller can skip this row
                            if verbose:
                                print(f"[SYNC_CLOUD_DB][ERROR] HTTP error downloading {url}: {he}")
                            return None
                        except Exception as e:
                            if verbose:
                                print(f"[SYNC_CLOUD_DB][ERROR] Error downloading {url}: {e}")
                            return None

                    transcript_path = download(transcript_url, 'transcript')
                    if not transcript_path:
                        errors.append(f"Missing or inaccessible transcript for call {call_id_val}: {transcript_url}")
                        if verbose:
                            print(f"[SYNC_CLOUD_DB] Could not download transcript for call {call_id_val}")
                        continue

                    agent_audio_path = download(agent_audio_url, 'agent')
                    if not agent_audio_path:
                        errors.append(f"Missing or inaccessible agent audio for call {call_id_val}: {agent_audio_url}")
                        if verbose:
                            print(f"[SYNC_CLOUD_DB] Could not download agent audio for call {call_id_val}")
                        continue

                    emp_audio_path = None
                    if emp_audio_url:
                        emp_audio_path = download(emp_audio_url, 'emp')

                    # read transcript
                    with open(transcript_path, 'r', encoding='utf-8') as f:
                        transcript = f.read()

                    transcript_msgs = parse_transcript_lines(transcript)
                    sentiment_scores = get_agent_employee_sentiment(transcript_msgs)
                    agent_sentiment_percent = sentiment_scores['agent_sentiment_percent']
                    employee_sentiment_percent = sentiment_scores['employee_sentiment_percent']
                    analysis = {}
                    if transcript.strip():
                        for section, prompt in PROMPTS.items():
                            result_sec = generate_section(prompt, transcript)
                            if result_sec:
                                analysis[section] = result_sec
                            else:
                                analysis[section] = {"error": f"Failed to parse {section} output"}

                    avg_speech_rate_agent, segment_times_agent, segment_zcrs_agent = calculate_average_speech_rate(agent_audio_path)
                    avg_speech_rate_employee, segment_times_employee, segment_zcrs_employee = calculate_average_speech_rate(emp_audio_path) if emp_audio_path else (None, None, None)
                    calm_score = get_calm_score(agent_audio_path)
                    vad_times, valence_list, arousal_list, dominance_list = get_vad_over_time(agent_audio_path)
                    sentiment_flow = get_sentiment_flow(transcript_msgs)

                    context_score = (agent_sentiment_percent + (employee_sentiment_percent or 0)) / 2 / 10 if agent_sentiment_percent is not None else 0
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

                    def get_audio_duration(filepath):
                        try:
                            audio = AudioSegment.from_file(filepath)
                            return round(len(audio) / 1000, 2)
                        except Exception:
                            return None

                    duration = get_audio_duration(agent_audio_path)
                    department = None
                    topic = None
                    if analysis.get('call_summary'):
                        department = analysis['call_summary'].get('department')
                        topic = analysis['call_summary'].get('topic')

                    agent_name = extract_agent_name(transcript)
                    dash_chars = r"[-–—‒−]"
                    timestamps = []
                    for line in transcript.splitlines():
                        match = re.match(r"(\d{2}:\d{2}:\d{2})\s*" + dash_chars, line)
                        if match:
                            timestamps.append(match.group(1))
                    call_start = timestamps[0] if timestamps else datetime.utcnow().strftime('%H:%M:%S')
                    call_end = timestamps[-1] if len(timestamps) > 1 else None
                    arousal_threshold = 0.85
                    voice_elevation_freq = sum(1 for a in arousal_list if a > arousal_threshold)
                    sop_steps_followed = 0
                    if analysis.get('sop_adherence'):
                        sop = analysis['sop_adherence']
                        sop_steps = [sop.get('identity_verification'), sop.get('security_questions'), sop.get('two_factor_enabled'), sop.get('account_activity_review'), sop.get('security_best_practices')]
                        sop_steps_followed = sum(1 for s in sop_steps if s)

                    created_at_val = extract_call_timestamp(rowdict=rowdict, source_name=transcript_url)
                    if created_at_val is None:
                        created_at_val = datetime.utcnow()

                    doc = {
                        "agent_audio": os.path.basename(agent_audio_path),
                        "employee_audio": os.path.basename(emp_audio_path) if emp_audio_path else None,
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
                        "user_name": user_name,
                        "user_email": user_email,
                        "created_at": created_at_val,
                        "call_start": call_start,
                        "call_end": call_end,
                        "voice_elevation_freq": voice_elevation_freq,
                        "sop_steps_followed": sop_steps_followed
                    }

                    try:
                        self.insert_call(doc)
                        processed += 1
                        last_call_id = call_id_val
                    except Exception as e:
                        error_str = str(e).lower()
                        if 'duplicate entry' in error_str or 'violation of unique key constraint' in error_str:
                            if verbose:
                                print(f"[DEDUP] Duplicate call_id {doc.get('call_id')} not inserted.")
                        else:
                            if verbose:
                                print(f"[ERROR] Exception inserting doc: {e}")
                            errors.append(f"Error inserting doc for call {call_id_val}: {str(e)}")

                except Exception as e:
                    if verbose:
                        print(f"[SYNC_CLOUD_DB][ERROR] Exception processing row: {e}")
                    errors.append(f"Row processing error: {str(e)}")

            return processed, errors, last_call_id
        finally:
            session.close()

    def get_call_by_call_id(self, call_id):
        """
        Fetch a single call record by call_id. Returns dict or None.
        """
        session = self.Session()
        try:
            call = session.query(CallData).filter_by(call_id=call_id).first()
            if call:
                return {c.name: getattr(call, c.name) for c in call.__table__.columns}
            return None
        finally:
            session.close()

    def insert_call(self, call_dict):
        session = self.Session()
        try:
            call = CallData(**call_dict)
            session.add(call)
            session.commit()
            return call.id
        except Exception as e:
            session.rollback()
            raise
        finally:
            session.close()

    def get_all_calls(self, limit=None, offset=None):
        """
        Fetch all call records from the database, optionally paginated.
        Returns a list of dicts.
        """
        session = self.Session()
        try:
            query = session.query(CallData).order_by(CallData.created_at.desc())
            if offset is not None:
                query = query.offset(offset)
            if limit is not None:
                query = query.limit(limit)
            calls = query.all()
            result = []
            for call in calls:
                # Convert SQLAlchemy object to dict
                call_dict = {c.name: getattr(call, c.name) for c in call.__table__.columns}
                result.append(call_dict)
            return result
        finally:
            session.close()