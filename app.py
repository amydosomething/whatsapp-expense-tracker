from flask import Flask, request, session
from twilio.twiml.messaging_response import MessagingResponse
import google.generativeai as genai
import requests
import os
from datetime import datetime, timedelta
import json
import threading

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key-here')

# Configure Gemini API
genai.configure(api_key=os.environ.get('GEMINI_API_KEY'))
model = genai.GenerativeModel('gemini-2.0-flash-exp')

# Categories
STANDARD_CATEGORIES = ['Cable', 'Labour', 'Material Purchase', 'Fuel']

# Store pending expenses
pending_expenses = {}

def get_custom_categories():
    """Get all custom categories from Google Sheet"""
    try:
        apps_script_url = os.environ.get('APPS_SCRIPT_URL')
        print(f"[DEBUG] Apps Script URL: {apps_script_url}")
        
        response = requests.get(apps_script_url, params={'action': 'get_custom_categories'}, timeout=10)
        
        print(f"[DEBUG] Response status code: {response.status_code}")
        print(f"[DEBUG] Response text: {response.text}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"[DEBUG] Parsed JSON data: {data}")
            return data if isinstance(data, dict) else {}
        print(f"[DEBUG] Response not 200, returning empty dict")
        return {}
    except Exception as e:
        print(f"[DEBUG] Error getting custom categories: {e}")
        import traceback
        print(f"[DEBUG] Traceback: {traceback.format_exc()}")
        return {}

def save_custom_category(category_name):
    """Save custom category to Google Sheet"""
    try:
        apps_script_url = os.environ.get('APPS_SCRIPT_URL')
        
        payload = {
            'action': 'save_custom_category',
            'category_name': category_name
        }
        
        print(f"[DEBUG] Saving custom category with payload: {payload}")
        response = requests.post(apps_script_url, json=payload, timeout=10)
        
        print(f"[DEBUG] Save response status: {response.status_code}")
        print(f"[DEBUG] Save response text: {response.text}")
        
        return response.status_code == 200
    except Exception as e:
        print(f"[DEBUG] Error saving custom category: {e}")
        import traceback
        print(f"[DEBUG] Traceback: {traceback.format_exc()}")
        return False

def parse_date_with_gemini(date_input):
    """Use Gemini to parse any date format to DD-MM-YYYY"""
    
    current_date = datetime.now().strftime('%d-%m-%Y')
    current_year = datetime.now().year
    
    prompt = f"""
    Convert this date to DD-MM-YYYY format.
    
    Current date: {current_date}
    Current year: {current_year}
    
    Date input: "{date_input}"
    
    Rules:
    - If year is missing, use current year ({current_year})
    - If user says "today", use {current_date}
    - If user says "yesterday", calculate it
    - Handle formats like: 18 oct, 18/10/2025, 18-10-2025, 18 oct 2024, 18/10/24, etc.
    
    Return ONLY the date in DD-MM-YYYY format, nothing else.
    Example: 18-10-2025
    """
    
    try:
        response = model.generate_content(prompt)
        result = response.text.strip()
        
        # Validate it's in correct format
        datetime.strptime(result, '%d-%m-%Y')
        return result
    except Exception as e:
        print(f"Error parsing date with Gemini: {e}")
        return None

def parse_expense_with_gemini(message):
    """Use Gemini to parse the expense message"""
    
    current_year = datetime.now().year
    custom_cats = get_custom_categories()
    custom_cat_names = list(custom_cats.keys())
    
    print(f"[DEBUG] Custom categories retrieved: {custom_cat_names}")
    
    categories_str = STANDARD_CATEGORIES + custom_cat_names
    print(f"[DEBUG] All available categories for Gemini: {categories_str}")
    
    prompt = f"""
    Parse this expense message and extract the following information in JSON format:
    - date (convert to DD-MM-YYYY format. Current year is {current_year}. If year not mentioned, use current year. If no date mentioned, return "missing")
    - amount (just the number, no currency symbols)
    - description (brief description of the expense)
    - category (choose ONE from: {', '.join(categories_str)})
    
    Category rules:
    - Cable: any cable, wire, electrical cables, cable specs (100 sqmm, 4 core, etc)
    - Labour: labour work, advance labour, worker payments, worker names
    - Material Purchase: cement, bricks, sand, paint, screws, epoxy, adhesives, pipes, fittings, any construction materials
    - Fuel: petrol, diesel, CNG, fuel, any vehicle fuel
    {f'- Available custom categories: {", ".join(custom_cat_names)}' if custom_cat_names else ''}
    
    If you're not 100% sure about the category, return "uncertain" for category.
    
    Message: "{message}"
    
    Return ONLY a valid JSON object with keys: date, amount, description, category
    Example: {{"date": "18-10-2025", "amount": "2000", "description": "Labour work", "category": "Labour"}}
    If date not mentioned: {{"date": "missing", "amount": "2000", "description": "Labour work", "category": "Labour"}}
    """
    
    try:
        response = model.generate_content(prompt)
        result = response.text.strip()
        
        print(f"[DEBUG] Gemini raw response: {result}")
        
        # Remove code blocks if present
        if result.startswith('```'):
            result = result.split('```')[1]
            if result.startswith('json'):
                result = result[4:]
        result = result.strip()
        
        parsed_data = json.loads(result)
        print(f"[DEBUG] Parsed expense data: {parsed_data}")
        return parsed_data
    except Exception as e:
        print(f"[DEBUG] Error parsing with Gemini: {e}")
        import traceback
        print(f"[DEBUG] Traceback: {traceback.format_exc()}")
        return None

def add_expense_to_sheet(date, amount, description, category):
    """Send expense data to Google Apps Script"""
    try:
        apps_script_url = os.environ.get('APPS_SCRIPT_URL')
        
        payload = {
            'action': 'add_expense',
            'date': date,
            'amount': amount,
            'description': description,
            'category': category
        }
        
        print(f"[DEBUG] Adding expense with payload: {payload}")
        response = requests.post(apps_script_url, json=payload, timeout=10)
        
        print(f"[DEBUG] Add expense response status: {response.status_code}")
        print(f"[DEBUG] Add expense response text: {response.text}")
        
        if response.status_code == 200:
            return True
        else:
            print(f"[DEBUG] Apps Script error: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        print(f"[DEBUG] Error sending to Apps Script: {e}")
        import traceback
        print(f"[DEBUG] Traceback: {traceback.format_exc()}")
        return False

def get_sheet_data():
    """Retrieve data from Google Sheet"""
    try:
        apps_script_url = os.environ.get('APPS_SCRIPT_URL')
        response = requests.get(apps_script_url, params={'action': 'get_expenses'}, timeout=10)
        
        print(f"[DEBUG] Get sheet data response status: {response.status_code}")
        
        if response.status_code == 200:
            return response.json()
        return None
    except Exception as e:
        print(f"[DEBUG] Error getting sheet data: {e}")
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
    category_totals = {}
    
    for row in filtered_data:
        cat = row.get('category', 'Other')
        if cat not in category_totals:
            category_totals[cat] = {'total': 0, 'count': 0}
        category_totals[cat]['total'] += float(row.get('amount', 0))
        category_totals[cat]['count'] += 1
    
    return category_totals, len(filtered_data)

@app.route('/webhook', methods=['POST'])
def whatsapp_webhook():
    """Handle incoming WhatsApp messages"""
    incoming_msg = request.values.get('Body', '').strip()
    from_number = request.values.get('From', '')
    
    print(f"\n[DEBUG] ===== NEW MESSAGE =====")
    print(f"[DEBUG] From: {from_number}")
    print(f"[DEBUG] Message: {incoming_msg}")
    print(f"[DEBUG] Pending expenses: {pending_expenses}")
    
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
            for cat in sorted(stats.keys()):
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
    
    # Check if user is responding to a pending request
    if from_number in pending_expenses:
        pending = pending_expenses[from_number]
        print(f"[DEBUG] User has pending request: {pending}")
        
        if pending['waiting_for'] == 'date':
            # Parse the date with Gemini
            final_date = parse_date_with_gemini(incoming_msg)
            
            if not final_date:
                msg.body("❌ I couldn't understand that date. Please try again or type 'cancel'")
                return str(resp)
            
            # Now check if category is uncertain
            if pending.get('category') == 'uncertain':
                pending['date'] = final_date
                pending['waiting_for'] = 'category'
                pending_expenses[from_number] = pending
                
                custom_cats = get_custom_categories()
                cat_list = "1. Cable\n2. Labour\n3. Material Purchase\n4. Fuel"
                if custom_cats:
                    for idx, cat_name in enumerate(custom_cats.keys(), start=5):
                        cat_list += f"\n{idx}. {cat_name}"
                
                msg.body(f"📋 Please choose a category:\n\n{cat_list}\n\nReply with the number or category name.")
                return str(resp)
            
            # Send acknowledgment first
            msg.body(f"✅ Adding expense...\n\nDate: {final_date}\nAmount: ₹{pending['amount']}\nDescription: {pending['description']}\nCategory: {pending['category']}")
            
            response_to_send = str(resp)
            
            # Add to sheet in background
            def add_in_background():
                add_expense_to_sheet(
                    final_date,
                    pending['amount'],
                    pending['description'],
                    pending['category']
                )
            
            thread = threading.Thread(target=add_in_background)
            thread.start()
            
            del pending_expenses[from_number]
            return response_to_send
        
        elif pending['waiting_for'] == 'category':
            # User is choosing category
            category_input = incoming_msg.strip()
            
            custom_cats = get_custom_categories()
            
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
            
            # Add custom categories to map
            for idx, cat_name in enumerate(custom_cats.keys(), start=5):
                category_map[str(idx)] = cat_name
                category_map[cat_name.lower()] = cat_name
            
            print(f"[DEBUG] Category map: {category_map}")
            print(f"[DEBUG] User input: {category_input.lower()}")
            
            final_category = category_map.get(category_input.lower())
            
            if not final_category:
                custom_cats = get_custom_categories()
                cat_list = "1. Cable\n2. Labour\n3. Material Purchase\n4. Fuel"
                if custom_cats:
                    for idx, cat_name in enumerate(custom_cats.keys(), start=5):
                        cat_list += f"\n{idx}. {cat_name}"
                
                msg.body(f"❌ Invalid category. Please choose:\n\n{cat_list}\n\nOr type 'cancel'")
                return str(resp)
            
            print(f"[DEBUG] Final category selected: {final_category}")
            
            # Send acknowledgment first
            msg.body(f"✅ Adding expense...\n\nDate: {pending['date']}\nAmount: ₹{pending['amount']}\nDescription: {pending['description']}\nCategory: {final_category}")
            
            response_to_send = str(resp)
            
            # Add to sheet in background
            def add_in_background():
                add_expense_to_sheet(
                    pending['date'],
                    pending['amount'],
                    pending['description'],
                    final_category
                )
            
            thread = threading.Thread(target=add_in_background)
            thread.start()
            
            del pending_expenses[from_number]
            return response_to_send
        
        elif pending['waiting_for'] == 'custom_category':
            # User is specifying custom category name
            custom_category = incoming_msg.strip()
            
            if not custom_category or len(custom_category) < 2:
                msg.body("❌ Please provide a valid category name (at least 2 characters)")
                return str(resp)
            
            print(f"[DEBUG] Saving custom category: {custom_category}")
            
            # Save this custom category to sheet
            save_custom_category(custom_category)
            
            # Send acknowledgment first
            msg.body(f"✅ Adding expense...\n\nDate: {pending['date']}\nAmount: ₹{pending['amount']}\nDescription: {pending['description']}\nCategory: {custom_category}")
            
            response_to_send = str(resp)
            
            # Add to sheet in background
            def add_in_background():
                add_expense_to_sheet(
                    pending['date'],
                    pending['amount'],
                    pending['description'],
                    custom_category
                )
            
            thread = threading.Thread(target=add_in_background)
            thread.start()
            
            del pending_expenses[from_number]
            return response_to_send
    
    # Parse new expense message
    print(f"[DEBUG] Parsing new expense message")
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
        msg.body("📅 Please enter the date for this expense:\n(Example: today, yesterday, 18 oct, 18/10/2025)")
        return str(resp)
    
    # Check if category is uncertain
    if parsed_data.get('category') == 'uncertain':
        pending_expenses[from_number] = {
            'date': parsed_data['date'],
            'amount': parsed_data['amount'],
            'description': parsed_data['description'],
            'waiting_for': 'category'
        }
        custom_cats = get_custom_categories()
        cat_list = "1. Cable\n2. Labour\n3. Material Purchase\n4. Fuel"
        if custom_cats:
            for idx, cat_name in enumerate(custom_cats.keys(), start=5):
                cat_list += f"\n{idx}. {cat_name}"
        
        msg.body(f"📋 Please choose a category:\n\n{cat_list}\n\nReply with the number or category name.")
        return str(resp)
    
    # Check if category needs custom name
    if parsed_data.get('category') == 'Other':
        pending_expenses[from_number] = {
            'date': parsed_data['date'],
            'amount': parsed_data['amount'],
            'description': parsed_data['description'],
            'waiting_for': 'custom_category'
        }
        msg.body("📝 Please specify the custom category name:\n(Example: Food, Transport, Maintenance, etc.)")
        return str(resp)
    
    # Send acknowledgment first
    msg.body(f"✅ Adding expense...\n\nDate: {parsed_data['date']}\nAmount: ₹{parsed_data['amount']}\nDescription: {parsed_data['description']}\nCategory: {parsed_data['category']}")
    
    response_to_send = str(resp)
    
    # Add to sheet in background
    def add_in_background():
        add_expense_to_sheet(
            parsed_data['date'],
            parsed_data['amount'],
            parsed_data['description'],
            parsed_data['category']
        )
    
    thread = threading.Thread(target=add_in_background)
    thread.start()
    
    return response_to_send

@app.route('/', methods=['GET'])
def home():
    return "WhatsApp Expense Tracker is running! 🚀"

@app.route('/health', methods=['GET'])
def health():
    return {"status": "healthy"}, 200

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
