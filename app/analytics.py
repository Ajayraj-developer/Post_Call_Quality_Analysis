import re
import json
import requests

# ============================================
# OLLAMA LOCAL CONFIG
# ============================================

MODEL = "llama3.2:latest"

ENDPOINT = "http://localhost:11434/api/generate"

# ============================================
# PROMPTS
# ============================================

PROMPTS = {

    # ========================================
    # CALL SUMMARY
    # ========================================

    "call_summary": """
Generate a detailed call analysis.

Return ONLY valid JSON.

{
  "summary": "Detailed summary of the call with issue, agent action, and resolution",
  "generic_issue": "Description of issue without PII",
  "agent_actions": "Steps the agent took to assist",
  "resolution_status": "How the issue was resolved",
  "customer_feedback": "Customer's expressed feedback",
  "topic": "One short-form topic: use ONLY one of ['AcctSec','Billing','Tech','LoginHelp','Usage','Feedback','SubsMgmt','Order']",
  "product": "One-word product involved in the call",
  "resolved": "Yes/No",
  "feedback_received": "Yes/No",
  "department": "Extract department in which agent works, if none then use IT Support"
}

Rules:
- Return ONLY valid JSON
- No markdown
- No explanations

Call transcript:
{transcript}
""",

    # ========================================
    # SOP ADHERENCE
    # ========================================

    "sop_adherence": """
Analyze the call transcript for SOP adherence.

Return ONLY valid JSON.

{
  "identity_verification": true,
  "security_questions": true,
  "two_factor_enabled": true,
  "account_activity_review": true,
  "security_best_practices": true
}

STRICT RULES:
- Output ONLY valid JSON
- Every field MUST be true or false
- NEVER use:
  - Yes/No
  - checkmarks
  - symbols
  - explanations

Definitions:

identity_verification:
true if agent verifies identity using email, phone, OTP, account ID, DOB, or name.

security_questions:
true if agent asks authentication/security verification questions.

two_factor_enabled:
true if agent discusses, recommends, or enables 2FA/OTP.

account_activity_review:
true if agent reviews login activity, suspicious activity, or account usage.

security_best_practices:
true if agent advises password reset, strong passwords, phishing awareness, or account safety.

If evidence appears once, return true.
If not mentioned, return false.

Call transcript:
{transcript}
""",

    # ========================================
    # COMMUNICATION QUALITY
    # ========================================

    "communication_quality": """
Evaluate communication quality from the transcript.

Return ONLY valid JSON.

{
  "language_fluency": "Assessment of grammar, clarity, and coherence",
  "clear_responses": true,
  "tone_professionalism": "Assessment of professionalism",
  "empathetic_tone": true,
  "communication_effectiveness": "Assessment of effectiveness",
  "problem_solving": true
}

Rules:
- Boolean fields MUST be true or false only
- Return ONLY valid JSON
- No markdown
- No explanations

Call transcript:
{transcript}
""",

    # ========================================
    # CONVERSATION TIMELINE
    # ========================================

"conversation_timeline": """
Extract conversation timeline from transcript.

Return ONLY valid JSON.

{
  "timeline": [
    {
      "timestamp": "00:00",
      "speaker": "Agent",
      "content": "Actual conversation text",
      "analysis": "Short analysis"
    }
  ],
  "total_resolution_time": "HH:MM:SS"
}

STRICT RULES:

speaker value MUST be EXACTLY one of:
- "Agent"
- "Customer"

DO NOT use:
- "User"
- "Caller"
- "Client"
- "Representative"
- "Support"
- "Agent:"
- "Customer:"
- "[REDACTED]"

ONLY redact:
- phone numbers
- emails
- account numbers
- addresses

DO NOT redact:
- greetings
- troubleshooting
- issue descriptions
- support discussion

BAD:
"[REDACTED]"

GOOD:
"Customer reported login issue for account [REDACTED]"

Keep conversation readable.
Do NOT over-redact.

Return ONLY valid JSON.

Call transcript:
{transcript}
"""
}

# ============================================
# JSON PARSER
# ============================================

def parse_llm_output(output):

    try:
        return json.loads(output)

    except json.JSONDecodeError:

        try:
            json_match = re.search(r'\{[\s\S]*\}', output)

            if json_match:
                return json.loads(json_match.group(0))

        except Exception:
            pass

    return {
        "error": "Invalid JSON",
        "raw_output": output
    }

# ============================================
# GENERATE SECTION
# ============================================

def generate_section(prompt_template, transcript):

    prompt_text = prompt_template.replace("{transcript}", transcript)

    response = requests.post(
        ENDPOINT,
        headers={
            "Content-Type": "application/json"
        },
        json={
            "model": MODEL,
            "prompt": prompt_text,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0,
                "top_p": 0.8,
                "repeat_penalty": 1.1,
                "num_predict": 2048
            }
        }
    )

    if response.status_code == 200:

        result = response.json()

        result_text = result["response"]

        return parse_llm_output(result_text)

    else:

        print("Error:", response.status_code)
        print(response.text)

        return None