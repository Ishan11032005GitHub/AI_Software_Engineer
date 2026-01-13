from config import GEMINI_API_KEY
import google.generativeai as genai

genai.configure(api_key=GEMINI_API_KEY)

model = genai.GenerativeModel("gemini-1.5-pro")


def generate_review(diff: str, file_name: str) -> str:
    prompt = f"""
You are a senior code reviewer. Review the following patch:

FILE: {file_name}
DIFF:
{diff}

Return bullet points only:
- potential risks
- edge cases not handled
- better alternative if minimal
- test cases to add
- final quick verdict (LGTM / Needs Fix)
"""
    resp = model.generate_content(prompt)
    return resp.text.strip()
