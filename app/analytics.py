import re
import json
import requests

API_KEY = "AIzaSyB55VmTYkOD3ovTgkVsChblm-V1A2rxdRo"
ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={API_KEY}"

PROMPTS = {
    "call_summary": """Generate analysis in JSON format with these exact keys:
{
  "summary": "Detailed summary of the call with issue, agent action, and resolution",
  "generic_issue": "Description of issue without PII",
  "agent_actions": "Steps the agent took to assist",
  "resolution_status": "How the issue was resolved",
  "customer_feedback": "Customer's expressed feedback",
  "topic": "One short-form topic: use 'AcctSec', 'Billing', 'Tech', 'LoginHelp', 'Usage', 'Feedback', 'SubsMgmt', or 'Order'",
  "product": "one short-form word :Product involved in the call",
  "resolved": "Yes/No",
  "feedback_received": "Yes/No",
  "department":"Extract department in which agent works, if none then  use IT Support"
}

Call transcript: {transcript}""",

    "sop_adherence": """Evaluate SOP adherence in JSON format with these exact keys:
{
  "identity_verification": true,
  "security_questions": true,
  "two_factor_enabled": true,
  "account_activity_review": true,
  "security_best_practices": true
}

Call transcript: {transcript}""",

"communication_quality": """Evaluate the communication quality based on the following transcript. Provide your response in JSON format using the specified keys:

{
  "language_fluency": "Assess the clarity, grammar, and coherence of the speaker's language (e.g., 'Clear and well-structured')",
  "clear_responses": true,  // Was the information conveyed in a straightforward and understandable manner?
  "tone_professionalism": "Describe the tone used during the interaction (e.g., 'Polite and professional')",
  "empathetic_tone": true,  // Did the speaker demonstrate empathy or understanding?
  "communication_effectiveness": "Summarize how effectively the message was delivered and received (e.g., 'Highly effective with clear resolutions')",
  "problem_solving": true   // Did the speaker make an effort to address and resolve the issue?
}


Call transcript: {transcript}""",

"conversation_timeline": """Extract timeline in JSON format with these exact keys:
{
  "timeline": [
    {
      "timestamp": "00:00",
      "speaker": "Agent" or "Customer", // Use only one of these values
      "content": "What was said (REDACT ALL PII such as names, phone numbers, emails, account numbers, addresses, etc. Replace with [REDACTED])",
      "analysis": "Short analysis of this segment (do not include PII)"
    }
  ],
  "total_resolution_time": "HH:MM:SS"
}
IMPORTANT: In all 'content' and 'analysis' fields, redact any PII (names, phone numbers, emails, account numbers, addresses, etc.) by replacing with [REDACTED].
Ensure that 'speaker' is strictly either "Agent" or "Customer" — do not combine or modify these labels.
Call transcript: {transcript}"""
}

def parse_llm_output(output):
    """Try extracting JSON from Gemini response text"""
    try:
        # Direct JSON parse
        return json.loads(output)
    except json.JSONDecodeError:
        try:
            json_match = re.search(r'{[\s\S]*}', output)
            if json_match:
                return json.loads(json_match.group(0))
        except:
            pass
    return None

def generate_section(prompt_template, transcript):
    prompt_text = prompt_template.replace("{transcript}", transcript)

    response = requests.post(
        ENDPOINT,
        headers={"Content-Type": "application/json"},
        json={
            "contents": [{"parts": [{"text": prompt_text}]}]
        }
    )

    if response.status_code == 200:
        result_text = response.json()['candidates'][0]['content']['parts'][0]['text']
        return parse_llm_output(result_text)
    else:
        print("Error:", response.status_code, response.text)
        return None