import os
import asyncio
from dotenv import load_dotenv
from telethon import TelegramClient

load_dotenv()

async def find_chats():
    client = TelegramClient('session', int(os.getenv('API_ID')), os.getenv('API_HASH'))
    await client.start(phone=os.getenv('PHONE_NUMBER'))
    
    print("\n" + "="*70)
    print("📋 YOUR TELEGRAM CHATS")
    print("="*70 + "\n")
    
    me = await client.get_me()
    print(f"✅ Logged in as: {me.first_name} {me.last_name or ''}")
    print(f"   Your User ID: {me.id}")
    print(f"\n💡 To send to 'Saved Messages', use: TARGET_CHAT_ID={me.id}\n")
    print("="*70 + "\n")
    
    async for dialog in client.iter_dialogs():
        entity_type = type(dialog.entity).__name__
        
        # Add emoji based on type
        if entity_type == "User":
            emoji = "👤"
        elif entity_type == "Chat":
            emoji = "👥"
        elif entity_type == "Channel":
            emoji = "📢"
        else:
            emoji = "❓"
        
        print(f"{emoji} {dialog.name}")
        print(f"   ID: {dialog.id}")
        print(f"   Type: {entity_type}")
        
        # Show if it's where you want to send or monitor from
        if dialog.id == -1005124028935:
            print(f"   ⭐ THIS IS YOUR TARGET_CHAT_ID")
        if dialog.id == -1002153543401:
            print(f"   ⭐ THIS IS IN YOUR MONITORED_CHATS")
        
        print("-" * 70)
    
    await client.disconnect()
    print("\n✅ Done! Copy the correct IDs to your .env file\n")

if __name__ == "__main__":
    asyncio.run(find_chats())

