from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
import google.generativeai as genai
import requests
import os
from datetime import datetime

app = Flask(__name__)

# Configure Gemini API
genai.configure(api_key=os.environ.get('GEMINI_API_KEY'))
model = genai.GenerativeModel('gemini-2.5-flash')

def parse_expense_with_gemini(message):
    """Use Gemini to parse the expense message"""
    prompt = f"""
    Parse this expense message and extract the following information in JSON format:
    - date (in YYYY-MM-DD format, if not specified use today's date: {datetime.now().strftime('%Y-%m-%d')})
    - amount (just the number, no currency symbols)
    - description (brief description of the expense)
    
    Message: "{message}"
    
    Return ONLY a valid JSON object with keys: date, amount, description
    Example: {{"date": "2025-10-02", "amount": "4000", "description": "Labour work"}}
    """
    
    try:
        response = model.generate_content(prompt)
        # Extract JSON from response
        result = response.text.strip()
        # Remove markdown code blocks if present
        if result.startswith('```'):
            result = result.split('```')[1]
            if result.startswith('json'):
                result = result[4:]
        result = result.strip()
        
        import json
        parsed_data = json.loads(result)
        
        # Convert date from YYYY-MM-DD to DD-MM-YYYY
        if parsed_data and 'date' in parsed_data:
            try:
                date_obj = datetime.strptime(parsed_data['date'], '%Y-%m-%d')
                parsed_data['date'] = date_obj.strftime('%d-%m-%Y')
            except:
                pass  # If conversion fails, keep original format
        
        return parsed_data
    except Exception as e:
        print(f"Error parsing with Gemini: {e}")
        return None

def add_expense_to_sheet(date, amount, description):
    """Send expense data to Google Apps Script"""
    try:
        apps_script_url = os.environ.get('APPS_SCRIPT_URL')
        
        payload = {
            'date': date,
            'amount': amount,
            'description': description
        }
        
        response = requests.post(apps_script_url, json=payload, timeout=10)
        
        if response.status_code == 200:
            return True
        else:
            print(f"Apps Script error: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        print(f"Error sending to Apps Script: {e}")
        return False

@app.route('/webhook', methods=['POST'])
def whatsapp_webhook():
    """Handle incoming WhatsApp messages"""
    incoming_msg = request.values.get('Body', '').strip()
    from_number = request.values.get('From', '')
    
    resp = MessagingResponse()
    msg = resp.message()
    
    if not incoming_msg:
        msg.body("Please send me an expense message! Example: 'Paid 4000 rs for labour work on 2nd Oct 2025'")
        return str(resp)
    
    # Parse the message using Gemini
    parsed_data = parse_expense_with_gemini(incoming_msg)
    
    if parsed_data and all(k in parsed_data for k in ['date', 'amount', 'description']):
        # Add to Google Sheets via Apps Script
        success = add_expense_to_sheet(
            parsed_data['date'],
            parsed_data['amount'],
            parsed_data['description']
        )
        
        if success:
            msg.body(f"✅ Expense added!\n\nDate: {parsed_data['date']}\nAmount: ₹{parsed_data['amount']}\nDescription: {parsed_data['description']}")
        else:
            msg.body("❌ Sorry, failed to add expense to sheet. Please try again.")
    else:
        msg.body("❌ I couldn't understand that. Please try again.\n\nExample: 'Paid 500 for groceries today'")
    
    return str(resp)

@app.route('/', methods=['GET'])
def home():
    return "WhatsApp Expense Tracker is running! 🚀"

@app.route('/health', methods=['GET'])
def health():
    return {"status": "healthy"}, 200

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
