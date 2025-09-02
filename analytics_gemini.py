
import re
import requests
import json

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
  "feedback_received": "Yes/No"
}

Call transcript: {transcript}""",

    "sop_adherence": """Evaluate SOP adherence in JSON format with these exact keys:
{
  "identity_verification": true/false,
  "security_questions": true/false,
  "two_factor_enabled": true/false,
  "account_activity_review": true/false,
  "security_best_practices": true/false
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
      "content": "What was said",
      "analysis": "Short analysis of this segment"
    }
  ],
  "total_resolution_time": "HH:MM:SS"
}
Ensure that 'speaker' is strictly either "Agent" or "Customer" — do not combine or modify these labels.
Call transcript: {transcript}"""
}

def parse_llm_output(output):
    """Parse LLM output trying multiple methods to extract JSON"""
    try:
        # Try direct JSON parse first
        return json.loads(output)
    except json.JSONDecodeError:
        try:
            # Try extracting JSON from markdown code block
            json_match = re.search(r'```json\n(.*?)\n```', output, re.DOTALL)
            if json_match:
                return json.loads(json_match.group(1))
        except:
            pass
    return None

def generate_section(prompt, transcript):
    response = requests.post(
        "http://localhost:11434/api/generate",
        json={
            "model": "llama3",
            "prompt": prompt.replace("{transcript}", transcript),
            "stream": False,
            "format": "json"  # Request JSON output if supported
        }
    )
    return parse_llm_output(response.json()['response'])