from sqlalchemy import create_engine, Column, Integer, String, Float, Text, DateTime, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import datetime
import os
from urllib.parse import quote_plus

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
                    "created_at": datetime.utcnow(),
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