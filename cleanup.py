#!/usr/bin/env python3
"""
Session Cleanup Script
======================

Use this if a user gets stuck with Telegram security blocks.

This script will:
1. Delete the user's session file
2. Clear temp login data
3. Reset their status in the database

After running this, the user can try /start again with a fresh state.
"""

import sqlite3
import os
import sys

def cleanup_user_session(user_id: int):
    """Clean up all session data for a user"""
    
    print(f"\n🔄 Cleaning up session for user {user_id}...\n")
    
    # 1. Delete session file
    session_file = f"sessions/user_{user_id}.session"
    if os.path.exists(session_file):
        os.remove(session_file)
        print(f"✅ Deleted session file: {session_file}")
    else:
        print(f"ℹ️  No session file found")
    
    # Also check for journal files
    journal_file = f"{session_file}-journal"
    if os.path.exists(journal_file):
        os.remove(journal_file)
        print(f"✅ Deleted journal file")
    
    # 2. Reset database status
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    
    # Update session status
    cursor.execute(
        "UPDATE users SET session_active = 0 WHERE user_id = ?",
        (user_id,)
    )
    
    rows_affected = cursor.rowcount
    conn.commit()
    conn.close()
    
    if rows_affected > 0:
        print(f"✅ Reset session status in database")
    else:
        print(f"⚠️  User {user_id} not found in database")
    
    print(f"\n✅ Cleanup complete for user {user_id}!")
    print(f"\n📝 Next steps:")
    print(f"   1. Wait 15-20 minutes (Telegram cooldown)")
    print(f"   2. User sends /start to the bot")
    print(f"   3. User follows setup process again")
    print(f"   4. Use a FRESH verification code")
    print(f"\n💡 Tip: Tell the user to be very quick with code entry!\n")

def list_users():
    """List all users in the database"""
    if not os.path.exists('bot_data.db'):
        print("❌ Database not found!")
        return
    
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT user_id, username, session_active, registered_at 
        FROM users 
        ORDER BY registered_at DESC
    """)
    
    users = cursor.fetchall()
    conn.close()
    
    if not users:
        print("\nℹ️  No users found in database")
        return
    
    print("\n" + "="*70)
    print("📋 USERS IN DATABASE")
    print("="*70)
    print(f"{'User ID':<15} {'Username':<20} {'Session':<10} {'Registered'}")
    print("-"*70)
    
    for user_id, username, session_active, registered_at in users:
        status = "🟢 Active" if session_active else "🔴 Inactive"
        date = registered_at[:10] if registered_at else "Unknown"
        print(f"{user_id:<15} {username:<20} {status:<10} {date}")
    
    print("="*70 + "\n")

def cleanup_all_sessions():
    """Clean up ALL user sessions (nuclear option)"""
    print("\n⚠️  WARNING: This will delete ALL user sessions!")
    confirm = input("Type 'yes' to confirm: ")
    
    if confirm.lower() != 'yes':
        print("❌ Cancelled")
        return
    
    # Delete all session files
    if os.path.exists('sessions'):
        import glob
        session_files = glob.glob('sessions/*.session*')
        
        for file in session_files:
            os.remove(file)
            print(f"✅ Deleted: {file}")
    
    # Reset all users in database
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET session_active = 0")
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    
    print(f"\n✅ Cleaned up {affected} users")
    print(f"📝 All users will need to /start and authenticate again\n")

def main():
    if not os.path.exists('bot_data.db'):
        print("❌ Database not found! Make sure you're in the bot directory.")
        sys.exit(1)
    
    print("""
╔══════════════════════════════════════════════════════╗
║                                                      ║
║          SESSION CLEANUP UTILITY                     ║
║                                                      ║
║  Use this to fix stuck login attempts                ║
║                                                      ║
╚══════════════════════════════════════════════════════╝
""")
    
    while True:
        print("\n📋 Options:")
        print("  1. List all users")
        print("  2. Clean up specific user")
        print("  3. Clean up ALL sessions (nuclear)")
        print("  4. Exit")
        
        choice = input("\nEnter choice (1-4): ").strip()
        
        if choice == '1':
            list_users()
        
        elif choice == '2':
            try:
                user_id = int(input("Enter user ID to clean up: ").strip())
                cleanup_user_session(user_id)
            except ValueError:
                print("❌ Invalid user ID")
        
        elif choice == '3':
            cleanup_all_sessions()
        
        elif choice == '4':
            print("\n👋 Goodbye!\n")
            break
        
        else:
            print("❌ Invalid choice")

if __name__ == "__main__":
    main()

