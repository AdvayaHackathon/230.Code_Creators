# call_reminder.py
from twilio.rest import Client

# Replace with your actual Twilio credentials
account_sid = 'Your account id'
auth_token = 'Your token'
twilio_number = 'Put your twilio number'  # Must be in E.164 format, e.g., '+14155552671'

client = Client(account_sid, auth_token)

def make_reminder_call(to_number, medicine_name):
    """
    Initiates a voice call reminder using Twilio.
    Example: make_reminder_call('+919876543210', 'Dolo 650')
    """

    try:
        # Fallback to TwiML if dynamic message fails
        call = client.calls.create(
            to=to_number,  # e.g., '+91XXXXXXXXXX'
            from_=twilio_number,
            twiml=f'<Response><Say voice="alice">Hello! This is a reminder to take your medicine: {medicine_name}. Please do not forget.</Say></Response>'
        )
        print(f"Call initiated with SID: {call.sid}")
    except Exception as e:
        print(f"Error initiating call: {e}")