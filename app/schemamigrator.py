import mysql.connector

# Connect to MySQL
conn = mysql.connector.connect(
    host="localhost",
    user="root",
    password="Subhash@2003",
    database="aivqa"
)
cursor = conn.cursor()

# Step 1: Alter table to ensure correct column types
alter_queries = [
    "ALTER TABLE calls MODIFY avg_speech_rate FLOAT",
    "ALTER TABLE calls MODIFY calm_score FLOAT",
    "ALTER TABLE calls MODIFY overall_score FLOAT",
    "ALTER TABLE calls MODIFY duration FLOAT",
    "ALTER TABLE calls MODIFY agent_sentiment_percent FLOAT",
    "ALTER TABLE calls MODIFY employee_sentiment_percent FLOAT"
]

for q in alter_queries:
    try:
        cursor.execute(q)
        print(f"[OK] Executed: {q}")
    except Exception as e:
        print(f"[WARN] Could not execute {q}: {e}")

# Step 2: Convert existing rows (if stored as strings)
update_queries = [
    "UPDATE calls SET avg_speech_rate = CAST(avg_speech_rate AS DECIMAL(10,4))",
    "UPDATE calls SET calm_score = CAST(calm_score AS DECIMAL(10,4))",
    "UPDATE calls SET overall_score = CAST(overall_score AS DECIMAL(10,4))",
    "UPDATE calls SET duration = CAST(duration AS DECIMAL(10,4))",
    "UPDATE calls SET agent_sentiment_percent = CAST(agent_sentiment_percent AS DECIMAL(10,4))",
    "UPDATE calls SET employee_sentiment_percent = CAST(employee_sentiment_percent AS DECIMAL(10,4))"
]

for q in update_queries:
    try:
        cursor.execute(q)
        print(f"[OK] Updated: {q}")
    except Exception as e:
        print(f"[WARN] Could not execute {q}: {e}")

conn.commit()
cursor.close()
conn.close()

print("✅ Migration completed successfully.")
