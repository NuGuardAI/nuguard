
import psycopg2
import os
import sys

# Add backend to path to import config
sys.path.append('/app/backend')
from db import get_db_connection

def run_sql_file(filename):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        with open(filename, 'r') as f:
            cur.execute(f.read())
        conn.commit()
        cur.close()
        conn.close()
        print(f"Successfully executed {filename}")
    except Exception as e:
        print(f"Error executing {filename}: {e}")

if __name__ == "__main__":
    run_sql_file('/workspaces/Healthcare-voice-agent/sql/populate_more_specialists.sql')
