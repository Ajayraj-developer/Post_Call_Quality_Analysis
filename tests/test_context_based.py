from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from context_based import parse_transcript_lines, get_agent_employee_sentiment
from data import extract_call_timestamp


def test_parse_transcript_lines_handles_same_line_speaker_format():
    transcript_text = """00:00:01 Agent: Hello, I need assistance.
00:00:03 Customer: I am having trouble logging in.
00:00:06 Agent: I can help with that.
"""

    messages = parse_transcript_lines(transcript_text)

    assert len(messages) == 3
    assert messages[0]['speaker'] == 'Agent'
    assert messages[1]['speaker'] == 'Customer'
    assert messages[0]['text'].endswith('Hello, I need assistance.')


def test_sentiment_scores_are_calculated_for_customer_and_agent_messages():
    transcript_text = """00:00:01 Agent: Hello, I can help you today.
00:00:03 Customer: I am frustrated and cannot access my account.
00:00:06 Agent: I am sorry for the trouble and will help.
"""

    messages = parse_transcript_lines(transcript_text)
    sentiment = get_agent_employee_sentiment(messages)

    assert sentiment['agent_sentiment_percent'] >= 0
    assert sentiment['employee_sentiment_percent'] >= 0
    assert sentiment['agent_sentiment_percent'] != 0 or sentiment['employee_sentiment_percent'] != 0


def test_parse_transcript_lines_handles_speaker_only_format():
    transcript_text = """Agent: Hello, I can help you today.
Customer: I am frustrated and cannot access my account.
Agent: I am sorry for the trouble and will help.
"""

    messages = parse_transcript_lines(transcript_text)

    assert len(messages) == 3
    assert messages[0]['speaker'] == 'Agent'
    assert messages[1]['speaker'] == 'Customer'


def test_extract_call_timestamp_prefers_real_call_time_from_db_and_filename():
    row = {
        'date_completed': '2026-06-29 05:11:58.817',
        'created_at': '2026-06-29 05:11:58.817',
    }
    timestamp = extract_call_timestamp(row, 'https://example.com/2026-06-29T05-11-46-725Z_709906.txt')

    assert timestamp is not None
    assert timestamp.year == 2026
    assert timestamp.month == 6
    assert timestamp.day == 29
    assert timestamp.hour == 5
    assert timestamp.minute == 11
