import os
import glob
import re

templates_dir = r"c:\Users\SaiSu\Documents\Postcall_deployment\Final\app\templates"

# The original button HTML we are looking for
old_button_pattern = re.compile(r'<button class="w-full flex items-center gap-3 px-4 py-3 rounded-lg text-left text-gray-900 hover:bg-gray-100">\s*<i data-lucide="radio" class="h-5 w-5"></i>\s*<span class="font-medium">Live Board</span>\s*</button>')

# My typo from previous edit
typo_pattern = re.compile(r'<a href="{{ url_for\(\'real_time_operations\'\) }}" class="w-full flex items-center gap-3 px-4 py-3 rounded-lg text-left {% if request\.endpoint == \'real_time_operations\' %}bg-primary text-primary-foreground shadow transition-all{% else %}text-gray-900 hover:bg-gray-100{% endif %}">\s*<i data-lucide="radio" class="h-5 w-5"></i>\s*<span class="font-medium">Live Board</span>\s*</a>>')

new_link = """<a href="{{ url_for('real_time_operations') }}" class="w-full flex items-center gap-3 px-4 py-3 rounded-lg text-left {% if request.endpoint == 'real_time_operations' %}bg-primary text-primary-foreground shadow transition-all{% else %}text-gray-900 hover:bg-gray-100{% endif %}">
            <i data-lucide="radio" class="h-5 w-5"></i>
            <span class="font-medium">Live Board</span>
          </a>"""

for filepath in glob.glob(os.path.join(templates_dir, "*.html")):
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    
    modified = False
    if old_button_pattern.search(content):
        content = old_button_pattern.sub(new_link, content)
        modified = True
    
    if typo_pattern.search(content):
        content = typo_pattern.sub(new_link, content)
        modified = True
        
    if modified:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"Updated {os.path.basename(filepath)}")
