import os
import requests
import time
import json
from datetime import datetime
import logging
import traceback

# ==================== SAFE CONFIGURATION ====================
print("🛒 Starting FreshMart Grocery Delivery Bot...")

# Get credentials from environment (SAFE)
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
SHEET_URL = os.environ.get('SHEET_URL')
ADMIN_CHAT_ID = os.environ.get('ADMIN_CHAT_ID')

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Check if environment variables are set
if not TELEGRAM_TOKEN:
    logger.error("❌ TELEGRAM_TOKEN environment variable not set!")
    exit(1)

if not ADMIN_CHAT_ID:
    logger.warning("⚠️ ADMIN_CHAT_ID not set, admin features disabled")

if not SHEET_URL:
    logger.warning("⚠️ SHEET_URL not set, Google Sheets disabled")

# Google Sheets setup (FIXED VERSION)
sheet = None
try:
    if SHEET_URL:
        import gspread
        from google.oauth2.service_account import Credentials
        
        # Get service account from environment
        service_account_json = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON')
        if service_account_json:
            try:
                # Parse the JSON service account
                creds_dict = json.loads(service_account_json)
                scope = ['https://www.googleapis.com/auth/spreadsheets']
                creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
                client = gspread.authorize(creds)
                
                # Open the spreadsheet by URL
                spreadsheet = client.open_by_url(SHEET_URL)
                sheet = spreadsheet.sheet1  # Use the first worksheet
                
                # Test connection by getting first row
                sheet.get_all_records()
                logger.info("✅ Google Sheets connected successfully!")
                
            except Exception as e:
                logger.error(f"❌ Google Sheets authentication failed: {e}")
                sheet = None
        else:
            logger.warning("⚠️ GOOGLE_SERVICE_ACCOUNT_JSON not provided - Google Sheets disabled")
            sheet = None
except ImportError:
    logger.error("❌ gspread not installed. Install with: pip install gspread")
    sheet = None
except Exception as e:
    logger.error(f"❌ Google Sheets setup failed: {e}")
    sheet = None

# Initialize sheet headers if sheet is connected
if sheet:
    try:
        # Check if headers exist, if not create them
        existing_headers = sheet.row_values(1)
        expected_headers = [
            'Order Date', 'Chat ID', 'Customer Name', 'Phone', 'Address',
            'Items', 'Quantities', 'Subtotal', 'Delivery Fee', 'Total',
            'Status', 'Special Instructions', 'Payment Method', 'Source', 'Order ID'
        ]
        
        if not existing_headers or existing_headers[0] != 'Order Date':
            sheet.insert_row(expected_headers, 1)
            logger.info("✅ Google Sheets headers initialized!")
    except Exception as e:
        logger.error(f"❌ Failed to initialize sheet headers: {e}")

# Grocery database
grocery_categories = {
    '🥦 Fresh Produce': {
        '🍎 Apples': {'price': 3.99, 'unit': 'kg'},
        '🍌 Bananas': {'price': 1.99, 'unit': 'kg'},
        '🥕 Carrots': {'price': 2.49, 'unit': 'kg'},
        '🥬 Spinach': {'price': 4.99, 'unit': 'bunch'},
        '🍅 Tomatoes': {'price': 3.49, 'unit': 'kg'}
    },
    '🥩 Meat & Poultry': {
        '🍗 Chicken Breast': {'price': 12.99, 'unit': 'kg'},
        '🥩 Beef Steak': {'price': 24.99, 'unit': 'kg'},
        '🐟 Salmon Fillet': {'price': 18.99, 'unit': 'kg'},
        '🥓 Bacon': {'price': 8.99, 'unit': 'pack'}
    },
    '🥛 Dairy & Eggs': {
        '🥛 Milk': {'price': 2.99, 'unit': 'liter'},
        '🧀 Cheese': {'price': 6.99, 'unit': 'block'},
        '🍳 Eggs': {'price': 4.99, 'unit': 'dozen'},
        '🧈 Butter': {'price': 3.99, 'unit': 'block'}
    }
}

user_carts = {}
user_sessions = {}
order_tracking = {}
last_update_id = 0  # Global variable to track updates

# ==================== ORDER TRACKING SYSTEM ====================
def generate_order_id():
    """Generate unique order ID"""
    return f"ORD{int(time.time())}"

def save_order_tracking(order_id, chat_id, customer_name, phone, address, cart, total, status="Pending"):
    """Save order to tracking system"""
    order_tracking[order_id] = {
        'chat_id': chat_id,
        'customer_name': customer_name,
        'phone': phone,
        'address': address,
        'cart': cart.copy(),  # Create a copy to avoid reference issues
        'total': total,
        'status': status,
        'created_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'updated_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    return order_id

def update_order_status(order_id, new_status, admin_note=""):
    """Update order status and notify customer"""
    if order_id not in order_tracking:
        return False
    
    order = order_tracking[order_id]
    old_status = order['status']
    order['status'] = new_status
    order['updated_at'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Update Google Sheets if connected
    if sheet:
        try:
            # Find the row with this order ID and update status
            records = sheet.get_all_records()
            for i, record in enumerate(records, start=2):  # start=2 because row 1 is headers
                if record.get('Order ID') == order_id:
                    sheet.update_cell(i, 11, new_status)  # Column 11 is Status
                    logger.info(f"✅ Updated order status in Google Sheets: {order_id} -> {new_status}")
                    break
        except Exception as e:
            logger.error(f"❌ Failed to update Google Sheets status: {e}")
    
    # Notify customer
    notify_customer_order_update(order_id, new_status, admin_note)
    
    logger.info(f"✅ Order {order_id} status updated: {old_status} → {new_status}")
    return True

def notify_customer_order_update(order_id, new_status, admin_note=""):
    """Notify customer about order status update"""
    order = order_tracking.get(order_id)
    if not order:
        return
    
    chat_id = order['chat_id']
    customer_name = order['customer_name']
    
    status_messages = {
        'Shipped': f"""🚚 Order Shipped! 

Hi {customer_name},

Your order #{order_id} is on the way! 

📦 Delivery Details:
• Order will arrive within 2 hours
• Please have ${order['total']:.2f} ready for cash payment
• Contact: 555-1234 if any issues

{f'📝 Note from store: {admin_note}' if admin_note else ''}

Thank you for choosing FreshMart! 🛒""",
        
        'Cancelled': f"""❌ Order Cancelled

Hi {customer_name},

We're sorry to inform you that your order #{order_id} has been cancelled.

{f'📝 Reason: {admin_note}' if admin_note else '📝 Reason: Unable to fulfill order at this time'}

We apologize for the inconvenience.

FreshMart Team 🛒""",
        
        'Delivered': f"""✅ Order Delivered! 

Hi {customer_name},

Your order #{order_id} has been successfully delivered!

Thank you for shopping with FreshMart! 🛒

We hope to serve you again soon! 🌟"""
    }
    
    message = status_messages.get(new_status)
    if message:
        send_message(chat_id, message)

# ==================== ADMIN ORDER MANAGEMENT ====================
def send_admin_order_notification(order_id, order_data):
    """Send new order notification to admin with action buttons"""
    if not ADMIN_CHAT_ID:
        return
        
    order_summary = create_admin_order_summary(order_id, order_data)
    
    admin_message = f"""🆕 NEW ORDER #{order_id}

{order_summary}

⏰ Order Time: {order_data['created_at']}
📊 Status: {order_data['status']}

Choose action:"""
    
    # Inline keyboard for admin actions
    inline_keyboard = [
        [
            {'text': '🚚 Mark as Shipped', 'callback_data': f'ship_{order_id}'},
            {'text': '❌ Cancel Order', 'callback_data': f'cancel_{order_id}'}
        ],
        [
            {'text': '✅ Mark Delivered', 'callback_data': f'deliver_{order_id}'},
            {'text': '📋 View Details', 'callback_data': f'details_{order_id}'}
        ]
    ]
    
    send_message(ADMIN_CHAT_ID, admin_message, inline_keyboard=inline_keyboard)

def create_admin_order_summary(order_id, order_data):
    """Create order summary for admin"""
    cart = order_data['cart']
    items_text = ""
    for item_name, details in cart.items():
        items_text += f"• {item_name} - {details['quantity']} {details['unit']}\n"
    
    summary = f"""👤 Customer: {order_data['customer_name']}
📞 Phone: {order_data['phone']}
📍 Address: {order_data['address']}

📦 Order Items:
{items_text}
💰 Total: ${order_data['total']:.2f}"""
    
    return summary

def handle_admin_callback(chat_id, callback_data):
    """Handle admin action callbacks"""
    if not ADMIN_CHAT_ID or str(chat_id) != ADMIN_CHAT_ID:
        send_message(chat_id, "❌ Unauthorized access.")
        return
    
    try:
        if callback_data.startswith('ship_'):
            order_id = callback_data[5:]
            if update_order_status(order_id, 'Shipped', 'Your order is on the way!'):
                send_message(chat_id, f"✅ Order #{order_id} marked as shipped! Customer notified.")
            else:
                send_message(chat_id, f"❌ Order #{order_id} not found.")
                
        elif callback_data.startswith('cancel_'):
            order_id = callback_data[7:]
            # Ask for cancellation reason
            user_sessions[chat_id] = {
                'step': 'awaiting_cancel_reason',
                'order_id': order_id
            }
            send_message(chat_id, f"📝 Please provide reason for cancelling order #{order_id}:")
            
        elif callback_data.startswith('deliver_'):
            order_id = callback_data[8:]
            if update_order_status(order_id, 'Delivered'):
                send_message(chat_id, f"✅ Order #{order_id} marked as delivered! Customer notified.")
            else:
                send_message(chat_id, f"❌ Order #{order_id} not found.")
                
        elif callback_data.startswith('details_'):
            order_id = callback_data[8:]
            order = order_tracking.get(order_id)
            if order:
                details = f"""📋 Order Details #{order_id}

Customer: {order['customer_name']}
Phone: {order['phone']}
Address: {order['address']}
Status: {order['status']}
Total: ${order['total']:.2f}
Created: {order['created_at']}
Updated: {order['updated_at']}

Items:"""
                for item_name, details in order['cart'].items():
                    details += f"\n• {item_name} - {order['cart'][item_name]['quantity']} {order['cart'][item_name]['unit']}"
                
                send_message(chat_id, details)
            else:
                send_message(chat_id, f"❌ Order #{order_id} not found.")
                
    except Exception as e:
        logger.error(f"❌ Admin callback error: {e}")
        send_message(chat_id, "❌ Error processing admin action.")

# ==================== ENHANCED MESSAGE HANDLING ====================
def send_message(chat_id, text, keyboard=None, inline_keyboard=None, parse_mode='HTML'):
    """Enhanced message sending with comprehensive error handling"""
    if not TELEGRAM_TOKEN:
        logger.error("❌ Cannot send message: TELEGRAM_TOKEN not set")
        return False
        
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            'chat_id': chat_id, 
            'text': text,
            'parse_mode': parse_mode
        }

        if keyboard:
            payload['reply_markup'] = json.dumps({
                'keyboard': keyboard,
                'resize_keyboard': True,
                'one_time_keyboard': False
            })
        elif inline_keyboard:
            payload['reply_markup'] = json.dumps({
                'inline_keyboard': inline_keyboard
            })

        response = requests.post(url, json=payload, timeout=10)
        
        if response.status_code != 200:
            logger.error(f"Telegram API error: {response.status_code} - {response.text}")
            return False
            
        return True
        
    except Exception as e:
        logger.error(f"❌ Error sending message: {e}")
        return False

# ==================== ENHANCED ORDER SUMMARY ====================
def create_enhanced_order_summary(customer_name, phone, address, cart, special_instructions=""):
    """Create a beautifully formatted order summary"""
    
    subtotal = sum(details['price'] * details['quantity'] for details in cart.values())
    delivery_fee = 0 if subtotal >= 50 else 5
    total = subtotal + delivery_fee
    
    items_text = ""
    for item_name, details in cart.items():
        item_total = details['price'] * details['quantity']
        items_text += f"• {item_name}\n"
        items_text += f"  ${details['price']}/{details['unit']} × {details['quantity']} = ${item_total:.2f}\n"
    
    summary = f"""🛒 ORDER SUMMARY

👤 Customer Details:
Name: {customer_name}
Phone: {phone}
Address: {address}

📦 Order Items:
{items_text}
💵 Pricing:
Subtotal: ${subtotal:.2f}
Delivery Fee: ${delivery_fee:.2f}
{'🎉 FREE DELIVERY (Order > $50)' if delivery_fee == 0 else f'🎯 Add ${50 - subtotal:.2f} more for FREE delivery!'}
💰 TOTAL: ${total:.2f}

{f'📝 Special Instructions: {special_instructions}' if special_instructions else '📝 Special Instructions: None'}
    
⏰ Expected Delivery: Within 2 hours
🕐 Order Time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}"""
    
    return summary, total

# ==================== FIXED SHEET SAVING ====================
def save_order_to_sheet(chat_id, customer_name, phone, address, cart, special_instructions="", order_id=""):
    """Save order to Google Sheets - FIXED VERSION"""
    logger.info(f"📦 Order received: {customer_name}, ${sum(details['price'] * details['quantity'] for details in cart.values()):.2f}")
    
    # Try to save to Google Sheets if available
    if sheet:
        try:
            subtotal = sum(details['price'] * details['quantity'] for details in cart.values())
            delivery_fee = 0 if subtotal >= 50 else 5
            total = subtotal + delivery_fee

            # Format items and quantities
            items_list = []
            quantities_list = []
            for item_name, details in cart.items():
                items_list.append(item_name)
                quantities_list.append(f"{details['quantity']} {details['unit']}")

            # Prepare order data
            order_data = [
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),  # Order Date
                str(chat_id),                                  # Chat ID
                customer_name,                                 # Customer Name
                phone,                                         # Phone
                address,                                       # Address
                ", ".join(items_list),                         # Items
                ", ".join(quantities_list),                    # Quantities
                f"${subtotal:.2f}",                           # Subtotal
                f"${delivery_fee:.2f}",                       # Delivery Fee
                f"${total:.2f}",                              # Total
                "Pending",                                     # Status
                special_instructions,                         # Special Instructions
                "Cash on Delivery",                           # Payment Method
                "Telegram Bot",                               # Source
                order_id                                      # Order ID
            ]

            # Append to sheet
            sheet.append_row(order_data)
            logger.info("✅ Order saved to Google Sheets successfully!")
            return True
            
        except Exception as e:
            logger.error(f"❌ Google Sheets save failed: {e}")
            logger.error(f"❌ Error details: {traceback.format_exc()}")
            return False
    else:
        logger.info("ℹ️ Google Sheets not connected, order saved locally only")
        return True

# ==================== CASH ON DELIVERY PROCESSING ====================
def process_cash_on_delivery(chat_id, customer_name, phone, address, cart, special_instructions):
    """Process cash on delivery order - FIXED VERSION"""
    try:
        # Create enhanced order summary
        order_summary, total = create_enhanced_order_summary(
            customer_name, phone, address, cart, special_instructions
        )
        
        # Generate order ID and save to tracking
        order_id = generate_order_id()
        save_order_tracking(order_id, chat_id, customer_name, phone, address, cart, total, "Pending")
        
        # Save to Google Sheets (with proper error handling)
        sheets_success = save_order_to_sheet(
            chat_id, customer_name, phone, address, cart, 
            special_instructions, order_id
        )
        
        if not sheets_success:
            logger.warning("⚠️ Order saved locally but Google Sheets failed")
        
        # Send confirmation to customer
        confirmation = f"""✅ Order Confirmed! 🎉

Thank you {customer_name}!

{order_summary}

📦 Order ID: #{order_id}
💵 Payment: Cash on Delivery
💸 Please have ${total:.2f} ready for our delivery driver.

We'll notify you when your order ships! 🚚

We're preparing your fresh groceries! 🥦"""
        
        send_message(chat_id, confirmation)
        
        # Notify admin with action buttons
        try:
            order_data = order_tracking[order_id]
            send_admin_order_notification(order_id, order_data)
        except Exception as e:
            logger.warning(f"⚠️ Admin notification failed: {e}")
        
        # Clear cart and session
        if chat_id in user_carts:
            user_carts[chat_id] = {}
        user_sessions[chat_id] = {'step': 'main_menu'}
        
        logger.info(f"✅ COD order completed successfully for {customer_name}, Order ID: {order_id}")
        return True
            
    except Exception as e:
        logger.error(f"❌ Critical error in COD order: {e}")
        logger.error(f"❌ Error details: {traceback.format_exc()}")
        send_message(chat_id, "❌ Sorry, there was an error processing your order. Please try again.")
        return False

# ==================== BOT HANDLERS ====================
def handle_start(chat_id):
    welcome = """🛒 Welcome to FreshMart Grocery Delivery! 🛒

🌟 <b>Fresh Groceries Delivered to Your Doorstep!</b> 🌟

🚚 Free Delivery on orders over $50
⏰ Delivery Hours: 7 AM - 10 PM Daily  
💰 Payment: Cash on Delivery Only
📦 Real-time Order Tracking
📊 Automatic Order Logging

<b>What would you like to do?</b>"""

    keyboard = [
        [{'text': '🛍️ Shop Groceries'}, {'text': '🛒 My Cart'}],
        [{'text': '📦 Track Order'}, {'text': '📞 Contact Store'}],
        [{'text': 'ℹ️ Store Info'}]
    ]

    send_message(chat_id, welcome, keyboard=keyboard)
    user_sessions[chat_id] = {'step': 'main_menu'}

def show_categories(chat_id):
    categories = """📋 Grocery Categories

Choose a category to start shopping:"""

    keyboard = [
        [{'text': '🥦 Fresh Produce'}, {'text': '🥩 Meat & Poultry'}],
        [{'text': '🥛 Dairy & Eggs'}, {'text': '🔙 Main Menu'}]
    ]

    send_message(chat_id, categories, keyboard=keyboard)

def show_category_items(chat_id, category):
    if category not in grocery_categories:
        send_message(chat_id, "Category not found. Please choose from the menu.")
        return

    items_text = f"{category}\n\nSelect an item to add to cart:"
    items = grocery_categories[category]
    inline_keyboard = []

    for item_name, details in items.items():
        button_text = f"{item_name} - ${details['price']}/{details['unit']}"
        inline_keyboard.append([{
            'text': button_text,
            'callback_data': f"add_{item_name}"
        }])

    inline_keyboard.append([
        {'text': '🔙 Back to Categories', 'callback_data': 'back_categories'},
        {'text': '🛒 View Cart', 'callback_data': 'view_cart'}
    ])

    send_message(chat_id, items_text, inline_keyboard=inline_keyboard)
    user_sessions[chat_id] = {'step': 'browsing_category', 'current_category': category}

def handle_add_to_cart(chat_id, item_name):
    item_details = None
    for category, items in grocery_categories.items():
        if item_name in items:
            item_details = items[item_name]
            break

    if not item_details:
        send_message(chat_id, "Item not found. Please select from the menu.")
        return

    if chat_id not in user_carts:
        user_carts[chat_id] = {}

    if item_name in user_carts[chat_id]:
        user_carts[chat_id][item_name]['quantity'] += 1
    else:
        user_carts[chat_id][item_name] = {
            'price': item_details['price'],
            'unit': item_details['unit'],
            'quantity': 1
        }

    response = f"✅ Added to Cart!\n\n{item_name}\n${item_details['price']}/{item_details['unit']}\n\nWhat would you like to do next?"

    keyboard = [
        [{'text': '🛒 View Cart'}, {'text': '📋 Continue Shopping'}],
        [{'text': '🚚 Checkout'}, {'text': '🔙 Main Menu'}]
    ]

    send_message(chat_id, response, keyboard=keyboard)

def show_cart(chat_id):
    if chat_id not in user_carts or not user_carts[chat_id]:
        cart_text = "🛒 Your cart is empty!\n\nStart shopping to add some delicious groceries! 🥦"
        keyboard = [
            [{'text': '🛍️ Start Shopping'}, {'text': '🔙 Main Menu'}]
        ]
        send_message(chat_id, cart_text, keyboard=keyboard)
        return

    cart = user_carts[chat_id]
    total = 0
    cart_text = "🛒 Your Shopping Cart\n\n"

    for item_name, details in cart.items():
        item_total = details['price'] * details['quantity']
        total += item_total
        cart_text += f"• {item_name}\n"
        cart_text += f"  ${details['price']}/{details['unit']} × {details['quantity']} = ${item_total:.2f}\n\n"

    cart_text += f"💵 Subtotal: ${total:.2f}"
    delivery_fee = 0 if total >= 50 else 5
    final_total = total + delivery_fee

    cart_text += f"\n🚚 Delivery: ${delivery_fee:.2f}"
    cart_text += f"\n💰 Total: ${final_total:.2f}"

    if total < 50:
        cart_text += f"\n\n🎯 Add ${50 - total:.2f} more for FREE delivery!"
    else:
        cart_text += f"\n\n✅ You qualify for FREE delivery!"

    keyboard = [
        [{'text': '➕ Add More Items'}, {'text': '🗑️ Clear Cart'}],
        [{'text': '🚚 Checkout Now'}, {'text': '📋 Continue Shopping'}],
        [{'text': '🔙 Main Menu'}]
    ]

    send_message(chat_id, cart_text, keyboard=keyboard)

def handle_checkout(chat_id):
    if chat_id not in user_carts or not user_carts[chat_id]:
        send_message(chat_id, "Your cart is empty! Please add items first.")
        show_categories(chat_id)
        return

    send_message(chat_id, "🚚 Let's get your order delivered!\n\nPlease provide your full name:")
    user_sessions[chat_id] = {'step': 'awaiting_name'}

def handle_callback_query(chat_id, callback_data):
    if callback_data.startswith('add_'):
        item_name = callback_data[4:]
        handle_add_to_cart(chat_id, item_name)
    elif callback_data == 'back_categories':
        show_categories(chat_id)
    elif callback_data == 'view_cart':
        show_cart(chat_id)
    elif callback_data.startswith(('ship_', 'cancel_', 'deliver_', 'details_')):
        handle_admin_callback(chat_id, callback_data)

def get_updates(offset=None):
    """Get updates from Telegram with proper error handling"""
    global last_update_id
    
    if not TELEGRAM_TOKEN:
        return None
        
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    params = {'timeout': 30, 'offset': offset or last_update_id + 1}
        
    try:
        response = requests.get(url, params=params, timeout=35)
        if response.status_code == 200:
            data = response.json()
            if data.get('ok') and data.get('result'):
                # Update the last_update_id to the highest update_id received
                updates = data['result']
                if updates:
                    last_update_id = max(update['update_id'] for update in updates)
                return data
            return None
        else:
            if response.status_code == 409:
                logger.error("❌ Telegram API Error 409: Another bot instance is running with the same token!")
                logger.error("💡 Solution: Stop any other running instances of this bot")
            else:
                logger.error(f"Telegram API error: {response.status_code}")
            return None
    except Exception as e:
        logger.error(f"get_updates error: {e}")
        return None

def handle_message(chat_id, text):
    try:
        if text == '/start':
            handle_start(chat_id)
        elif text == '🛍️ Shop Groceries':
            show_categories(chat_id)
        elif text == '🛒 My Cart':
            show_cart(chat_id)
        elif text == '📦 Track Order':
            # Show user's recent orders
            user_orders = []
            for order_id, order in order_tracking.items():
                if order['chat_id'] == chat_id:
                    user_orders.append((order_id, order))
            
            if user_orders:
                track_text = "📦 Your Orders:\n\n"
                for order_id, order in user_orders[-5:]:  # Show last 5 orders
                    status_emoji = {
                        'Pending': '⏳',
                        'Shipped': '🚚', 
                        'Delivered': '✅',
                        'Cancelled': '❌'
                    }.get(order['status'], '📦')
                    
                    track_text += f"{status_emoji} Order #{order_id}\n"
                    track_text += f"Status: {order['status']}\n"
                    track_text += f"Total: ${order['total']:.2f}\n"
                    track_text += f"Date: {order['created_at']}\n\n"
                send_message(chat_id, track_text)
            else:
                send_message(chat_id, "📦 You don't have any orders yet. Start shopping! 🛍️")
        elif text == '🔙 Main Menu':
            handle_start(chat_id)
        elif text == '📋 Continue Shopping':
            show_categories(chat_id)
        elif text == '➕ Add More Items':
            show_categories(chat_id)
        elif text == '🗑️ Clear Cart':
            if chat_id in user_carts:
                user_carts[chat_id] = {}
            send_message(chat_id, "🛒 Your cart has been cleared!")
            show_categories(chat_id)
        elif text == '🚚 Checkout Now' or text == '🚚 Checkout':
            handle_checkout(chat_id)
        elif text in grocery_categories:
            show_category_items(chat_id, text)
        elif user_sessions.get(chat_id, {}).get('step') == 'awaiting_name':
            customer_name = text
            user_sessions[chat_id] = {'step': 'awaiting_phone', 'customer_name': customer_name}
            send_message(chat_id, f"👋 Thanks {customer_name}! Now please provide your phone number for delivery updates:")
        elif user_sessions.get(chat_id, {}).get('step') == 'awaiting_phone':
            user_phone = text
            customer_name = user_sessions[chat_id]['customer_name']
            user_sessions[chat_id] = {'step': 'awaiting_address', 'customer_name': customer_name, 'phone': user_phone}
            send_message(chat_id, "📦 Great! Now please provide your delivery address:")
        elif user_sessions.get(chat_id, {}).get('step') == 'awaiting_address':
            user_address = text
            customer_name = user_sessions[chat_id]['customer_name']
            user_phone = user_sessions[chat_id]['phone']
            user_sessions[chat_id] = {'step': 'awaiting_instructions', 'customer_name': customer_name, 'phone': user_phone, 'address': user_address}
            send_message(chat_id, "📝 Any special delivery instructions?\n\n(e.g., 'Leave at door', 'Call before delivery', or type 'None'):")
        elif user_sessions.get(chat_id, {}).get('step') == 'awaiting_instructions':
            special_instructions = text if text.lower() != 'none' else ""
            session_data = user_sessions[chat_id]
            
            # Process cash on delivery order
            process_cash_on_delivery(
                chat_id,
                session_data['customer_name'],
                session_data['phone'],
                session_data['address'],
                user_carts[chat_id],
                special_instructions
            )
        elif user_sessions.get(chat_id, {}).get('step') == 'awaiting_cancel_reason':
            # Admin providing cancellation reason
            order_id = user_sessions[chat_id].get('order_id')
            if order_id and update_order_status(order_id, 'Cancelled', text):
                send_message(chat_id, f"✅ Order #{order_id} cancelled! Customer notified with your reason.")
            else:
                send_message(chat_id, f"❌ Failed to cancel order #{order_id}")
            user_sessions[chat_id] = {'step': 'main_menu'}
        elif text == '📞 Contact Store':
            send_message(chat_id, "📞 FreshMart Contact Info:\n\n🏪 Store: FreshMart Grocery\n📞 Phone: 555-1234\n📍 Address: 123 Main Street\n⏰ Hours: 7 AM - 10 PM Daily")
        elif text == 'ℹ️ Store Info':
            store_info = f"""🏪 FreshMart Grocery

🌟 Your trusted local grocery store!

🚚 Free delivery on orders over $50
💰 Cash on delivery only
⏰ Fast 2-hour delivery
🥦 Fresh produce daily
📞 Call: 555-1234

{'📊 Orders automatically logged to Google Sheets' if sheet else '📊 Order tracking enabled'}"""
            send_message(chat_id, store_info)
        else:
            # Handle any other text by showing main menu
            handle_start(chat_id)

    except Exception as e:
        logger.error(f"❌ Error handling message: {e}")
        send_message(chat_id, "❌ Sorry, an error occurred. Please try again.")
        handle_start(chat_id)

def main():
    # Check environment variables first
    if not TELEGRAM_TOKEN:
        logger.error("❌ CRITICAL: TELEGRAM_TOKEN environment variable not set!")
        logger.error("💡 Set it in Railway → Variables tab")
        exit(1)

    # Log connection status
    logger.info("🛒 FreshMart Grocery Bot Started Successfully!")
    logger.info("📊 Features: Order Tracking, Admin Controls, Real-time Updates")
    logger.info("💰 Payment: Cash on Delivery Only")
    if sheet:
        logger.info("✅ Google Sheets Integration: ACTIVE")
    else:
        logger.info("❌ Google Sheets Integration: DISABLED - Check environment variables")
    logger.info("📱 Ready to take orders with professional order tracking!")

    while True:
        try:
            updates = get_updates()

            if updates and 'result' in updates:
                for update in updates['result']:
                    if 'message' in update and 'text' in update['message']:
                        chat_id = update['message']['chat']['id']
                        text = update['message']['text']
                        logger.info(f"📩 Message from {chat_id}: {text}")
                        handle_message(chat_id, text)

                    elif 'callback_query' in update:
                        callback = update['callback_query']
                        chat_id = callback['message']['chat']['id']
                        callback_data = callback['data']
                        logger.info(f"🔘 Callback from {chat_id}: {callback_data}")
                        handle_callback_query(chat_id, callback_data)

            time.sleep(1)
            
        except Exception as e:
            logger.error(f"❌ Main loop error: {e}")
            time.sleep(5)

if __name__ == '__main__':
    main()
