"""
Session Manager Module
======================
Handles individual user Telethon sessions for monitoring
"""

import os
import asyncio
from typing import Dict, Optional
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, FloodWaitError
from database import Database
import traceback

class SessionManager:
    """Manages multiple user Telethon sessions"""
    
    def __init__(self, db: Database, message_handler):
        self.db = db
        self.message_handler = message_handler  # Callback for handling messages
        self.user_clients: Dict[int, TelegramClient] = {}
        self.session_dir = "sessions"
        
        # Create sessions directory if not exists
        os.makedirs(self.session_dir, exist_ok=True)
    
    async def create_user_session(self, user_id: int, api_id: str, api_hash: str, 
                                  phone: str) -> tuple[bool, str]:
        """
        Create and authenticate a new user session
        Returns: (success: bool, message: str)
        """
        try:
            session_path = f"{self.session_dir}/user_{user_id}"
            
            # Create client
            client = TelegramClient(session_path, int(api_id), api_hash)
            
            # Connect
            await client.connect()
            
            # Check if already authorized
            if await client.is_user_authorized():
                self.user_clients[user_id] = client
                return (True, "Already authenticated!")
            
            # Send code request
            await client.send_code_request(phone)
            return (True, "Code sent! Waiting for verification code...")
            
        except FloodWaitError as e:
            return (False, f"Too many attempts. Please wait {e.seconds} seconds.")
        except Exception as e:
            print(f"❌ Error creating session for user {user_id}: {e}")
            traceback.print_exc()
            return (False, f"Error: {str(e)}")
    
    async def verify_code(self, user_id: int, api_id: str, api_hash: str,
                         phone: str, code: str) -> tuple[bool, str]:
        """
        Verify phone code and complete authentication
        Returns: (success: bool, message: str)
        """
        try:
            session_path = f"{self.session_dir}/user_{user_id}"
            
            # Recreate client
            client = TelegramClient(session_path, int(api_id), api_hash)
            await client.connect()
            
            # Sign in with code
            try:
                await client.sign_in(phone, code)
            except SessionPasswordNeededError:
                return (False, "2FA is enabled. Please disable it temporarily and try again.")
            except PhoneCodeInvalidError:
                return (False, "Invalid verification code. Please try again.")
            
            # Verify successful login
            if not await client.is_user_authorized():
                return (False, "Authentication failed. Please try again.")
            
            # Get user info
            me = await client.get_me()
            
            # Store client
            self.user_clients[user_id] = client
            
            # Update database
            self.db.update_user_credentials(user_id, api_id, api_hash, phone)
            
            # Start monitoring
            await self.setup_monitoring(user_id, client)
            
            return (True, f"✅ Authenticated as {me.first_name}! You can now add routes.")
            
        except Exception as e:
            print(f"❌ Error verifying code for user {user_id}: {e}")
            traceback.print_exc()
            return (False, f"Error: {str(e)}")
    
    async def setup_monitoring(self, user_id: int, client: TelegramClient):
        """Setup message monitoring for a user's client"""
        
        @client.on(events.NewMessage(incoming=True))
        async def user_message_handler(event):
            # Skip private messages
            if event.is_private:
                return
            
            # Pass to main message handler with user_id
            await self.message_handler(user_id, event)
        
        print(f"✅ Monitoring setup for user {user_id}")
    
    async def load_user_session(self, user_id: int) -> tuple[bool, str]:
        """Load existing user session from database"""
        try:
            # Get credentials from database
            credentials = self.db.get_user_credentials(user_id)
            if not credentials:
                return (False, "No session found. Please authenticate first.")
            
            api_id, api_hash, phone = credentials
            session_path = f"{self.session_dir}/user_{user_id}"
            
            # Check if session file exists
            if not os.path.exists(f"{session_path}.session"):
                return (False, "Session file missing. Please authenticate again.")
            
            # Create client
            client = TelegramClient(session_path, int(api_id), api_hash)
            await client.connect()
            
            # Verify still authorized
            if not await client.is_user_authorized():
                return (False, "Session expired. Please authenticate again.")
            
            # Store client
            self.user_clients[user_id] = client
            
            # Setup monitoring
            await self.setup_monitoring(user_id, client)
            
            me = await client.get_me()
            return (True, f"Loaded session for {me.first_name}")
            
        except Exception as e:
            print(f"❌ Error loading session for user {user_id}: {e}")
            traceback.print_exc()
            return (False, f"Error loading session: {str(e)}")
    
    async def load_all_sessions(self):
        """Load all active user sessions on bot startup"""
        print("🔄 Loading user sessions...")
        
        # Get all users with active sessions
        users = self.db.get_all_users()
        loaded = 0
        failed = 0
        
        for user in users:
            if user['session_active']:
                success, message = await self.load_user_session(user['user_id'])
                if success:
                    loaded += 1
                    print(f"  ✅ Loaded session for user {user['user_id']}")
                else:
                    failed += 1
                    print(f"  ❌ Failed to load session for user {user['user_id']}: {message}")
        
        print(f"✅ Loaded {loaded} sessions ({failed} failed)")
    
    def get_user_client(self, user_id: int) -> Optional[TelegramClient]:
        """Get Telethon client for a user"""
        return self.user_clients.get(user_id)
    
    async def get_chat_info(self, user_id: int, chat_id: int) -> Optional[dict]:
        """Get information about a chat using user's client"""
        client = self.get_user_client(user_id)
        if not client:
            return None
        
        try:
            entity = await client.get_entity(chat_id)
            
            # Format based on entity type
            if hasattr(entity, 'title'):
                return {
                    'id': chat_id,
                    'name': entity.title,
                    'type': 'channel' if hasattr(entity, 'broadcast') else 'group'
                }
            elif hasattr(entity, 'first_name'):
                name = f"{entity.first_name} {entity.last_name or ''}".strip()
                return {
                    'id': chat_id,
                    'name': name,
                    'type': 'user'
                }
            
            return None
            
        except Exception as e:
            print(f"❌ Error getting chat info: {e}")
            return None
    
    async def test_chat_access(self, user_id: int, chat_id: int) -> tuple[bool, str]:
        """Test if user has access to a chat"""
        client = self.get_user_client(user_id)
        if not client:
            return (False, "No active session. Please authenticate first.")
        
        try:
            entity = await client.get_entity(chat_id)
            
            if hasattr(entity, 'title'):
                name = entity.title
            elif hasattr(entity, 'first_name'):
                name = f"{entity.first_name} {entity.last_name or ''}".strip()
            else:
                name = "Unknown"
            
            return (True, f"✅ Access confirmed: {name}")
            
        except ValueError:
            return (False, "❌ Invalid chat ID. Make sure you're a member of this chat.")
        except Exception as e:
            return (False, f"❌ Error: {str(e)}")
    
    async def send_message(self, user_id: int, chat_id: int, message: str) -> bool:
        """Send a message using user's client"""
        client = self.get_user_client(user_id)
        if not client:
            return False
        
        try:
            await client.send_message(chat_id, message)
            return True
        except Exception as e:
            print(f"❌ Error sending message: {e}")
            return False
    
    async def forward_message(self, user_id: int, from_chat: int, to_chat: int, message_id: int) -> bool:
        """Forward a message using user's client"""
        client = self.get_user_client(user_id)
        if not client:
            return False
        
        try:
            await client.forward_messages(to_chat, message_id, from_chat)
            return True
        except Exception as e:
            print(f"❌ Error forwarding message: {e}")
            return False
    
    async def disconnect_user(self, user_id: int):
        """Disconnect a user's client"""
        if user_id in self.user_clients:
            try:
                await self.user_clients[user_id].disconnect()
                del self.user_clients[user_id]
                print(f"✅ Disconnected user {user_id}")
            except Exception as e:
                print(f"❌ Error disconnecting user {user_id}: {e}")
    
    async def disconnect_all(self):
        """Disconnect all user clients"""
        print("🔄 Disconnecting all user sessions...")
        
        for user_id in list(self.user_clients.keys()):
            await self.disconnect_user(user_id)
        
        print("✅ All sessions disconnected")
    
    def get_active_users_count(self) -> int:
        """Get count of users with active sessions"""
        return len(self.user_clients)
    
    def is_user_active(self, user_id: int) -> bool:
        """Check if user has active session"""
        return user_id in self.user_clients

