import os
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv
import openai

load_dotenv()

app = Flask(__name__, static_folder='static', static_url_path='')
CORS(app)

SYSTEM_PROMPT = """You are an expert swim recruiting analyst. You analyze swimmer profiles and provide detailed recruiting analysis for college coaches and swimmers.

Given swimmer data, you provide:
1. **Recruiting Tier**: Elite (D1 Power 5), D1 Mid-Major, D2, D3, NAIA, or Not Yet Recruitable
2. **Event Suitability**: Best events based on times and stroke
3. **Comparable Programs**: Specific college programs that would be a good fit
4. **Improvement Areas**: What the swimmer should work on
5. **Timeline Assessment**: When they might be ready for college recruiting
6. **Academic Fit Notes**: How academics factor into recruiting prospects

Be specific, honest, and actionable. Use actual NCAA and NAIA recruiting standards."""

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/api/analyze', methods=['POST'])
def analyze():
    data = request.json
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    swimmer_info = format_swimmer_info(data)

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return jsonify({'error': 'OpenAI API key not configured. Please add OPENAI_API_KEY to your environment variables.'}), 503

    try:
        client = openai.OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Please analyze this swimmer's recruiting prospects:\n\n{swimmer_info}"}
            ],
            temperature=0.7,
            max_tokens=1500
        )
        analysis = response.choices[0].message.content
        return jsonify({'analysis': analysis, 'swimmer': data})
    except openai.AuthenticationError:
        return jsonify({'error': 'Invalid OpenAI API key. Please check your OPENAI_API_KEY environment variable.'}), 401
    except openai.RateLimitError:
        return jsonify({'error': 'OpenAI rate limit reached. Please try again in a moment.'}), 429
    except Exception as e:
        return jsonify({'error': f'Analysis failed: {str(e)}'}), 500

def format_swimmer_info(data):
    lines = []
    if data.get('name'):
        lines.append(f"Name: {data['name']}")
    if data.get('gradYear'):
        lines.append(f"Graduation Year: {data['gradYear']}")
    if data.get('gender'):
        lines.append(f"Gender: {data['gender']}")
    if data.get('height'):
        lines.append(f"Height: {data['height']}")
    if data.get('weight'):
        lines.append(f"Weight: {data['weight']}")
    if data.get('primaryStroke'):
        lines.append(f"Primary Stroke: {data['primaryStroke']}")
    if data.get('events'):
        lines.append(f"\nBest Times:")
        for event in data['events']:
            if event.get('name') and event.get('time'):
                lines.append(f"  - {event['name']}: {event['time']}")
    if data.get('gpa'):
        lines.append(f"\nGPA: {data['gpa']}")
    if data.get('satAct'):
        lines.append(f"SAT/ACT: {data['satAct']}")
    if data.get('intendedMajor'):
        lines.append(f"Intended Major: {data['intendedMajor']}")
    if data.get('locationPreference'):
        lines.append(f"\nLocation Preference: {data['locationPreference']}")
    if data.get('schoolSizePreference'):
        lines.append(f"School Size Preference: {data['schoolSizePreference']}")
    if data.get('additionalInfo'):
        lines.append(f"\nAdditional Info: {data['additionalInfo']}")
    return '\n'.join(lines)

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
