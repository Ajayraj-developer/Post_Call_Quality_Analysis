"""
Delete utility for Post Call Quality Analysis database.
Provides methods to delete call records from the MySQL database.
"""
import os
import sys
from datetime import datetime
from dotenv import load_dotenv
from data import CallDataRepository

# Load environment variables
dotenv_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(dotenv_path)


class CallDeleter:
    """Utility class for deleting call records from the database."""
    
    def __init__(self, db_url=None):
        """
        Initialize the CallDeleter with database connection.
        
        Args:
            db_url: Database URL (optional, defaults to environment variables)
        """
        if db_url is None:
            db_user = os.environ.get('DB_USER')
            db_password = os.environ.get('DB_PASSWORD')
            db_host = os.environ.get('DB_HOST', 'localhost')
            db_name = os.environ.get('DB_NAME')
            db_url = f"mysql+pymysql://{db_user}:{db_password}@{db_host}/{db_name}"
        
        self.repo = CallDataRepository(db_url)
        self.session = self.repo.Session()
    
    def __del__(self):
        """Close the database session when object is destroyed."""
        if hasattr(self, 'session'):
            self.session.close()
    
    def delete_by_call_id(self, call_id, confirm=True):
        """
        Delete a call by its business call_id (6-digit ID).
        
        Args:
            call_id: The 6-digit call identifier
            confirm: Whether to ask for confirmation before deletion
        
        Returns:
            bool: True if deleted, False otherwise
        """
        from data import CallData
        
        # Fetch the call first to show details
        call = self.session.query(CallData).filter_by(call_id=call_id).first()
        
        if not call:
            print(f"❌ No call found with call_id: {call_id}")
            return False
        
        # Display call details
        print(f"\n📞 Call Details:")
        print(f"   Call ID: {call.call_id}")
        print(f"   Agent: {call.agent_name}")
        print(f"   Department: {call.department}")
        print(f"   Created: {call.created_at}")
        print(f"   Duration: {call.duration}s")
        print(f"   Overall Score: {call.overall_score}")
        
        # Confirm deletion
        if confirm:
            response = input(f"\n⚠️  Delete this call? (yes/no): ").strip().lower()
            if response not in ['yes', 'y']:
                print("❌ Deletion cancelled.")
                return False
        
        try:
            self.session.delete(call)
            self.session.commit()
            print(f"✅ Successfully deleted call with call_id: {call_id}")
            return True
        except Exception as e:
            self.session.rollback()
            print(f"❌ Error deleting call: {e}")
            return False
    
    def delete_by_id(self, db_id, confirm=True):
        """
        Delete a call by its database ID (primary key).
        
        Args:
            db_id: The database primary key ID
            confirm: Whether to ask for confirmation before deletion
        
        Returns:
            bool: True if deleted, False otherwise
        """
        from data import CallData
        
        call = self.session.query(CallData).filter_by(id=db_id).first()
        
        if not call:
            print(f"❌ No call found with database ID: {db_id}")
            return False
        
        # Display call details
        print(f"\n📞 Call Details:")
        print(f"   Database ID: {call.id}")
        print(f"   Call ID: {call.call_id}")
        print(f"   Agent: {call.agent_name}")
        print(f"   Department: {call.department}")
        print(f"   Created: {call.created_at}")
        
        # Confirm deletion
        if confirm:
            response = input(f"\n⚠️  Delete this call? (yes/no): ").strip().lower()
            if response not in ['yes', 'y']:
                print("❌ Deletion cancelled.")
                return False
        
        try:
            self.session.delete(call)
            self.session.commit()
            print(f"✅ Successfully deleted call with database ID: {db_id}")
            return True
        except Exception as e:
            self.session.rollback()
            print(f"❌ Error deleting call: {e}")
            return False
    
    def delete_multiple(self, call_ids, confirm=True):
        """
        Delete multiple calls by their call_ids.
        
        Args:
            call_ids: List of call_id values
            confirm: Whether to ask for confirmation before deletion
        
        Returns:
            tuple: (success_count, failed_count)
        """
        from data import CallData
        
        success_count = 0
        failed_count = 0
        
        print(f"\n🔍 Found {len(call_ids)} calls to delete")
        
        for call_id in call_ids:
            call = self.session.query(CallData).filter_by(call_id=call_id).first()
            
            if not call:
                print(f"❌ Call ID {call_id} not found")
                failed_count += 1
                continue
            
            print(f"   - {call_id} ({call.agent_name}, {call.created_at})")
        
        if confirm:
            response = input(f"\n⚠️  Delete all {len(call_ids)} calls? (yes/no): ").strip().lower()
            if response not in ['yes', 'y']:
                print("❌ Deletion cancelled.")
                return (0, 0)
        
        for call_id in call_ids:
            call = self.session.query(CallData).filter_by(call_id=call_id).first()
            
            if call:
                try:
                    self.session.delete(call)
                    self.session.commit()
                    success_count += 1
                    print(f"✅ Deleted: {call_id}")
                except Exception as e:
                    self.session.rollback()
                    print(f"❌ Failed to delete {call_id}: {e}")
                    failed_count += 1
        
        print(f"\n📊 Results: {success_count} deleted, {failed_count} failed")
        return (success_count, failed_count)
    
    def delete_by_agent(self, agent_name, confirm=True):
        """
        Delete all calls by a specific agent.
        
        Args:
            agent_name: Name of the agent
            confirm: Whether to ask for confirmation before deletion
        
        Returns:
            int: Number of calls deleted
        """
        from data import CallData
        
        calls = self.session.query(CallData).filter_by(agent_name=agent_name).all()
        
        if not calls:
            print(f"❌ No calls found for agent: {agent_name}")
            return 0
        
        print(f"\n🔍 Found {len(calls)} calls for agent: {agent_name}")
        for call in calls[:5]:  # Show first 5
            print(f"   - {call.call_id} ({call.created_at})")
        if len(calls) > 5:
            print(f"   ... and {len(calls) - 5} more")
        
        if confirm:
            response = input(f"\n⚠️  Delete all {len(calls)} calls? (yes/no): ").strip().lower()
            if response not in ['yes', 'y']:
                print("❌ Deletion cancelled.")
                return 0
        
        try:
            count = self.session.query(CallData).filter_by(agent_name=agent_name).delete()
            self.session.commit()
            print(f"✅ Successfully deleted {count} calls for agent: {agent_name}")
            return count
        except Exception as e:
            self.session.rollback()
            print(f"❌ Error deleting calls: {e}")
            return 0
    
    def delete_by_date_range(self, start_date, end_date, confirm=True):
        """
        Delete all calls within a date range.
        
        Args:
            start_date: Start date (datetime or string 'YYYY-MM-DD')
            end_date: End date (datetime or string 'YYYY-MM-DD')
            confirm: Whether to ask for confirmation before deletion
        
        Returns:
            int: Number of calls deleted
        """
        from data import CallData
        
        # Convert string dates to datetime if needed
        if isinstance(start_date, str):
            start_date = datetime.strptime(start_date, '%Y-%m-%d')
        if isinstance(end_date, str):
            end_date = datetime.strptime(end_date, '%Y-%m-%d')
        
        calls = self.session.query(CallData).filter(
            CallData.created_at >= start_date,
            CallData.created_at <= end_date
        ).all()
        
        if not calls:
            print(f"❌ No calls found between {start_date.date()} and {end_date.date()}")
            return 0
        
        print(f"\n🔍 Found {len(calls)} calls between {start_date.date()} and {end_date.date()}")
        for call in calls[:5]:  # Show first 5
            print(f"   - {call.call_id} ({call.agent_name}, {call.created_at})")
        if len(calls) > 5:
            print(f"   ... and {len(calls) - 5} more")
        
        if confirm:
            response = input(f"\n⚠️  Delete all {len(calls)} calls? (yes/no): ").strip().lower()
            if response not in ['yes', 'y']:
                print("❌ Deletion cancelled.")
                return 0
        
        try:
            count = self.session.query(CallData).filter(
                CallData.created_at >= start_date,
                CallData.created_at <= end_date
            ).delete()
            self.session.commit()
            print(f"✅ Successfully deleted {count} calls")
            return count
        except Exception as e:
            self.session.rollback()
            print(f"❌ Error deleting calls: {e}")
            return 0
    
    def list_all_calls(self, limit=10):
        """
        List all calls in the database (for reference).
        
        Args:
            limit: Maximum number of calls to display
        """
        from data import CallData
        
        calls = self.session.query(CallData).order_by(CallData.created_at.desc()).limit(limit).all()
        
        if not calls:
            print("❌ No calls found in database")
            return
        
        print(f"\n📋 Recent Calls (showing {len(calls)}):")
        print(f"{'Call ID':<10} {'Agent':<20} {'Department':<15} {'Created':<20} {'Score':<8}")
        print("-" * 85)
        
        for call in calls:
            created_str = call.created_at.strftime('%Y-%m-%d %H:%M') if call.created_at else 'N/A'
            score_str = f"{call.overall_score:.2f}" if call.overall_score is not None else 'N/A'
            print(f"{call.call_id or 'N/A':<10} {(call.agent_name or 'Unknown')[:19]:<20} {(call.department or 'N/A')[:14]:<15} {created_str:<20} {score_str:<8}")


def main():
    """Command-line interface for the delete utility."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Delete calls from Post Call Quality Analysis database')
    
    subparsers = parser.add_subparsers(dest='command', help='Command to execute')
    
    # List calls
    list_parser = subparsers.add_parser('list', help='List recent calls')
    list_parser.add_argument('--limit', type=int, default=10, help='Number of calls to show')
    
    # Delete by call_id
    call_id_parser = subparsers.add_parser('delete-call-id', help='Delete by call_id')
    call_id_parser.add_argument('call_id', type=str, help='Call ID to delete')
    call_id_parser.add_argument('--no-confirm', action='store_true', help='Skip confirmation')
    
    # Delete by database ID
    db_id_parser = subparsers.add_parser('delete-id', help='Delete by database ID')
    db_id_parser.add_argument('id', type=int, help='Database ID to delete')
    db_id_parser.add_argument('--no-confirm', action='store_true', help='Skip confirmation')
    
    # Delete multiple
    multi_parser = subparsers.add_parser('delete-multiple', help='Delete multiple calls')
    multi_parser.add_argument('call_ids', nargs='+', help='Call IDs to delete')
    multi_parser.add_argument('--no-confirm', action='store_true', help='Skip confirmation')
    
    # Delete by agent
    agent_parser = subparsers.add_parser('delete-agent', help='Delete all calls by agent')
    agent_parser.add_argument('agent_name', type=str, help='Agent name')
    agent_parser.add_argument('--no-confirm', action='store_true', help='Skip confirmation')
    
    # Delete by date range
    date_parser = subparsers.add_parser('delete-date-range', help='Delete calls in date range')
    date_parser.add_argument('start_date', type=str, help='Start date (YYYY-MM-DD)')
    date_parser.add_argument('end_date', type=str, help='End date (YYYY-MM-DD)')
    date_parser.add_argument('--no-confirm', action='store_true', help='Skip confirmation')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    deleter = CallDeleter()
    
    try:
        if args.command == 'list':
            deleter.list_all_calls(limit=args.limit)
        
        elif args.command == 'delete-call-id':
            deleter.delete_by_call_id(args.call_id, confirm=not args.no_confirm)
        
        elif args.command == 'delete-id':
            deleter.delete_by_id(args.id, confirm=not args.no_confirm)
        
        elif args.command == 'delete-multiple':
            deleter.delete_multiple(args.call_ids, confirm=not args.no_confirm)
        
        elif args.command == 'delete-agent':
            deleter.delete_by_agent(args.agent_name, confirm=not args.no_confirm)
        
        elif args.command == 'delete-date-range':
            deleter.delete_by_date_range(args.start_date, args.end_date, confirm=not args.no_confirm)
    
    except KeyboardInterrupt:
        print("\n\n❌ Operation cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
