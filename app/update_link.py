import os
import glob
import re

templates_dir = r"c:\Users\SaiSu\Documents\Postcall_deployment\Final\app\templates"

old_link = "https://vqa-live-fbapf3gudhdwgyes.southindia-01.azurewebsites.net/"
new_link = "https://wipgenai.lwpcoe.com/frontend_vqa_live/"

for filepath in glob.glob(os.path.join(templates_dir, "*.html")):
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    
    if old_link in content:
        content = content.replace(old_link, new_link)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"Updated {os.path.basename(filepath)}")
