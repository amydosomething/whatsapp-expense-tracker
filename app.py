from flask import Flask, request, session
from twilio.twiml.messaging_response import MessagingResponse
import google.generativeai as genai
import requests
import os
from datetime import datetime, timedelta
import json
import re

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key-here')

# Configure Gemini API
genai.configure(api_key=os.environ.get('GEMINI_API_KEY'))
model = genai.GenerativeModel('gemini-2.0-flash-exp')

# Categories
CATEGORIES = ['Cable', 'Labour', 'Material Purchase', 'Fuel']

# Store pending expenses (in production, use Redis or database)
pending_expenses = {}

def parse_expense_with_gemini(message, ask_category=False):
    """Use Gemini to parse the expense message"""
    
    if ask_category:
        category_prompt = f"""
        Classify this expense into ONE of these categories: {', '.join(CATEGORIES)}
        
        Message: "{message}"
        
        Return ONLY the category name, nothing else.
        """
        try:
            response = model.generate_content(category_prompt)
            category = response.text.strip()
            if category in CATEGORIES:
                return category
            return "uncertain"
        except:
            return "uncertain"
    
    prompt = f"""
    Parse this expense message and extract the following information in JSON format:
    - date (in YYYY-MM-DD format if mentioned, otherwise return "missing")
    - amount (just the number, no currency symbols)
    - description (brief description of the expense)
    - category (choose ONE from: Cable, Labour, Material Purchase, Fuel)
    
    Category rules:
    - Cable: any cable, wire, electrical cables, cable specs (100 sqmm, 4 core, etc)
    - Labour: labour work, advance labour, worker payments, worker names
    - Material Purchase: cement, bricks, sand, paint, screws, epoxy, adhesives, pipes, fittings, any construction materials
    - Fuel: petrol, diesel, CNG, fuel, any vehicle fuel
    
    If you're not 100% sure about the category, return "uncertain" for category.
    
    Message: "{message}"
    
    Return ONLY a valid JSON object with keys: date, amount, description, category
    Example: {{"date": "2025-10-15", "amount": "2000", "description": "Labour work", "category": "Labour"}}
    If date not mentioned: {{"date": "missing", "amount": "2000", "description": "Labour work", "category": "Labour"}}
    """
    
    try:
        response = model.generate_content(prompt)
        result = response.text.strip()
        
        if result.startswith('```'):
            result = result.split('```')[1]
            if result.startswith('json'):
                result = result[4:]
        result = result.strip()
        
        parsed_data = json.loads(result)
        
        # Convert date to DD-MM-YYYY format if present
        if parsed_data and 'date' in parsed_data and parsed_data['date'] != 'missing':
            # Try multiple date formats that Gemini might return
            date_formats_to_try = [
                '%Y-%m-%d',    # 2025-10-18
                '%d-%m-%Y',    # 18-10-2025
                '%d/%m/%Y',    # 18/10/2025
                '%Y/%m/%d',    # 2025/10/18
            ]
            
            date_converted = False
            for fmt in date_formats_to_try:
                try:
                    date_obj = datetime.strptime(parsed_data['date'], fmt)
                    parsed_data['date'] = date_obj.strftime('%d-%m-%Y')
                    date_converted = True
                    break
                except:
                    continue
            
            # If conversion failed, log it but continue
            if not date_converted:
                print(f"Warning: Could not convert date format: {parsed_data['date']}")
        
        return parsed_data
    except Exception as e:
        print(f"Error parsing with Gemini: {e}")
        return None

def parse_date_input(date_input):
    """Parse date input with smart year detection"""
    date_input = date_input.lower().strip()
    
    # Handle special keywords
    if date_input == 'today':
        return datetime.now().strftime('%d-%m-%Y')
    elif date_input == 'yesterday':
        return (datetime.now() - timedelta(days=1)).strftime('%d-%m-%Y')
    
    current_year = datetime.now().year
    
    # Try different date formats first
    date_formats = [
        '%d-%m-%Y',    # 19-10-2025
        '%d/%m/%Y',    # 19/10/2025
        '%d-%m-%y',    # 19-10-25
        '%d/%m/%y',    # 19/10/25
    ]
    
    for fmt in date_formats:
        try:
            date_obj = datetime.strptime(date_input, fmt)
            return date_obj.strftime('%d-%m-%Y')
        except:
            continue
    
    # Try parsing "19 oct 2024" format (WITH year)
    year_pattern = r'(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)\s+(\d{2,4})'
    year_match = re.search(year_pattern, date_input, re.IGNORECASE)
    
    if year_match:
        day = year_match.group(1)
        month_name = year_match.group(2)
        year = year_match.group(3)
        
        # Convert 2-digit year to 4-digit (25 -> 2025)
        if len(year) == 2:
            year = '20' + year
        
        # Try to parse with detected year
        try:
            date_str = f"{day} {month_name} {year}"
            date_obj = datetime.strptime(date_str, '%d %B %Y')  # Full month name
            return date_obj.strftime('%d-%m-%Y')
        except:
            try:
                date_obj = datetime.strptime(date_str, '%d %b %Y')  # Abbreviated month
                return date_obj.strftime('%d-%m-%Y')
            except:
                pass
    
    # Try parsing "19 oct" format (WITHOUT year - use current year)
    pattern = r'(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)'
    match = re.search(pattern, date_input, re.IGNORECASE)
    
    if match:
        day = match.group(1)
        month_name = match.group(2)
        
        # Try to parse with current year
        try:
            # Try full month name first
            date_str = f"{day} {month_name} {current_year}"
            date_obj = datetime.strptime(date_str, '%d %B %Y')
            return date_obj.strftime('%d-%m-%Y')
        except:
            try:
                # Try abbreviated month name
                date_str = f"{day} {month_name} {current_year}"
                date_obj = datetime.strptime(date_str, '%d %b %Y')
                return date_obj.strftime('%d-%m-%Y')
            except:
                pass
    
    # If nothing worked, return None
    return None

def add_expense_to_sheet(date, amount, description, category):
    """Send expense data to Google Apps Script"""
    try:
        apps_script_url = os.environ.get('APPS_SCRIPT_URL')
        
        payload = {
            'date': date,
            'amount': amount,
            'description': description,
            'category': category
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

def get_sheet_data():
    """Retrieve data from Google Sheet"""
    try:
        apps_script_url = os.environ.get('APPS_SCRIPT_URL')
        response = requests.get(apps_script_url, timeout=10)
        
        if response.status_code == 200:
            return response.json()
        return None
    except Exception as e:
        print(f"Error getting sheet data: {e}")
        return None

def calculate_stats(data, period='today'):
    """Calculate statistics based on period"""
    if not data:
        return None
    
    today = datetime.now()
    filtered_data = []
    
    for row in data:
        try:
            row_date = datetime.strptime(row['date'], '%d-%m-%Y')
            
            if period == 'today' and row_date.date() == today.date():
                filtered_data.append(row)
            elif period == 'week' and (today - row_date).days <= 7:
                filtered_data.append(row)
            elif period == 'month' and row_date.month == today.month and row_date.year == today.year:
                filtered_data.append(row)
        except:
            continue
    
    # Calculate totals by category
    category_totals = {cat: {'total': 0, 'count': 0} for cat in CATEGORIES}
    
    for row in filtered_data:
        cat = row.get('category', 'Material Purchase')
        category_totals[cat]['total'] += float(row.get('amount', 0))
        category_totals[cat]['count'] += 1
    
    return category_totals, len(filtered_data)

@app.route('/webhook', methods=['POST'])
def whatsapp_webhook():
    """Handle incoming WhatsApp messages"""
    incoming_msg = request.values.get('Body', '').strip()
    from_number = request.values.get('From', '')
    
    resp = MessagingResponse()
    msg = resp.message()
    
    if not incoming_msg:
        msg.body("Please send me an expense message!")
        return str(resp)
    
    # Check for commands
    command = incoming_msg.lower()
    
    # Cancel/Reset command
    if command in ['cancel', 'reset']:
        if from_number in pending_expenses:
            del pending_expenses[from_number]
            msg.body("🔄 Previous expense cancelled. You can now add a new expense.")
        else:
            msg.body("No pending expense to cancel.")
        return str(resp)
    
    # Stats commands
    if command in ['today', 'week', 'month']:
        data = get_sheet_data()
        if data:
            stats, total_transactions = calculate_stats(data, command)
            
            period_name = command.capitalize()
            response_text = f"📊 {period_name}'s Expenses\n\n"
            
            grand_total = 0
            for cat in CATEGORIES:
                if stats[cat]['total'] > 0:
                    response_text += f"{cat}: ₹{stats[cat]['total']:,.0f} ({stats[cat]['count']} transactions)\n"
                    grand_total += stats[cat]['total']
            
            response_text += f"\nTotal: ₹{grand_total:,.0f} ({total_transactions} transactions)"
            msg.body(response_text)
        else:
            msg.body("❌ Could not retrieve data from sheet.")
        return str(resp)
    
    if command in ['last', 'last expense']:
        data = get_sheet_data()
        if data and len(data) > 0:
            last = data[-1]
            response_text = f"🧾 Last Expense\n\nDate: {last['date']}\nAmount: ₹{last['amount']}\nDescription: {last['description']}\nCategory: {last['category']}"
            msg.body(response_text)
        else:
            msg.body("No expenses found.")
        return str(resp)
    
    # Check if user is responding to a pending date request
    if from_number in pending_expenses:
        pending = pending_expenses[from_number]
        
        if pending['waiting_for'] == 'date':
            # Parse the date with smart year detection
            final_date = parse_date_input(incoming_msg)
            
            if not final_date:
                msg.body("❌ Enter date for the previous expense in DD-MM-YYYY format or type 'cancel' to cancel the previous expense")
                return str(resp)
            
            # Now check if category is uncertain
            if pending.get('category') == 'uncertain':
                pending['date'] = final_date
                pending['waiting_for'] = 'category'
                pending_expenses[from_number] = pending
                
                msg.body(f"📋 Please choose a category:\n\n1. Cable\n2. Labour\n3. Material Purchase\n4. Fuel\n\nReply with the number or category name.")
                return str(resp)
            
            # Add to sheet
            success = add_expense_to_sheet(
                final_date,
                pending['amount'],
                pending['description'],
                pending['category']
            )
            
            if success:
                msg.body(f"✅ Expense added!\n\nDate: {final_date}\nAmount: ₹{pending['amount']}\nDescription: {pending['description']}\nCategory: {pending['category']}")
            else:
                msg.body("❌ Failed to add expense. Please try again.")
            
            del pending_expenses[from_number]
            return str(resp)
        
        elif pending['waiting_for'] == 'category':
            # User is choosing category
            category_input = incoming_msg.strip()
            
            # Map number or name to category
            category_map = {
                '1': 'Cable',
                '2': 'Labour', 
                '3': 'Material Purchase',
                '4': 'Fuel',
                'cable': 'Cable',
                'labour': 'Labour',
                'material': 'Material Purchase',
                'material purchase': 'Material Purchase',
                'fuel': 'Fuel'
            }
            
            final_category = category_map.get(category_input.lower())
            
            if not final_category:
                msg.body("❌ Invalid category. Please choose:\n1. Cable\n2. Labour\n3. Material Purchase\n4. Fuel\n\nOr type 'cancel' to cancel this expense.")
                return str(resp)
            
            # Add to sheet
            success = add_expense_to_sheet(
                pending['date'],
                pending['amount'],
                pending['description'],
                final_category
            )
            
            if success:
                msg.body(f"✅ Expense added!\n\nDate: {pending['date']}\nAmount: ₹{pending['amount']}\nDescription: {pending['description']}\nCategory: {final_category}")
            else:
                msg.body("❌ Failed to add expense. Please try again.")
            
            del pending_expenses[from_number]
            return str(resp)
    
    # Parse new expense message
    parsed_data = parse_expense_with_gemini(incoming_msg)
    
    if not parsed_data or not all(k in parsed_data for k in ['amount', 'description']):
        msg.body("❌ I couldn't understand that. Please try again.\n\nExample: 'Labour work 2000 on 15th Oct'")
        return str(resp)
    
    # Check if date is missing
    if parsed_data.get('date') == 'missing':
        pending_expenses[from_number] = {
            'amount': parsed_data['amount'],
            'description': parsed_data['description'],
            'category': parsed_data.get('category', 'uncertain'),
            'waiting_for': 'date'
        }
        msg.body("📅 Please enter the date for this expense:\n(Format: DD-MM-YYYY or 'today' or 'yesterday')")
        return str(resp)
    
    # Check if category is uncertain
    if parsed_data.get('category') == 'uncertain':
        pending_expenses[from_number] = {
            'date': parsed_data['date'],
            'amount': parsed_data['amount'],
            'description': parsed_data['description'],
            'waiting_for': 'category'
        }
        msg.body("📋 Please choose a category:\n\n1. Cable\n2. Labour\n3. Material Purchase\n4. Fuel\n\nReply with the number or category name.")
        return str(resp)
    
    # Add directly to sheet
    success = add_expense_to_sheet(
        parsed_data['date'],
        parsed_data['amount'],
        parsed_data['description'],
        parsed_data['category']
    )
    
    if success:
        msg.body(f"✅ Expense added!\n\nDate: {parsed_data['date']}\nAmount: ₹{parsed_data['amount']}\nDescription: {parsed_data['description']}\nCategory: {parsed_data['category']}")
    else:
        msg.body("❌ Failed to add expense. Please try again.")
    
    return str(resp)

@app.route('/', methods=['GET'])
def home():
    return "WhatsApp Expense Tracker is running! 🚀"

@app.route('/health', methods=['GET'])
def health():
    return {"status": "healthy"}, 200

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
