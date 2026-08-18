import os
import glob
import re

templates_dir = r"c:\Users\SaiSu\Documents\Postcall_deployment\Final\app\templates"

# The wrong link we added
wrong_link_pattern = re.compile(r'<a href="{{ url_for\(\'real_time_operations\'\) }}" class="w-full flex items-center gap-3 px-4 py-3 rounded-lg text-left \{\% if request\.endpoint == \'real_time_operations\' \%\}bg-primary text-primary-foreground shadow transition-all\{\% else \%\}text-gray-900 hover:bg-gray-100\{\% endif \%\}">\s*<i data-lucide="radio" class="h-5 w-5"></i>\s*<span class="font-medium">Live Board</span>\s*</a>')

correct_link = """<a href="https://vqa-live-fbapf3gudhdwgyes.southindia-01.azurewebsites.net/" target="_blank" class="w-full flex items-center gap-3 px-4 py-3 rounded-lg text-left text-gray-900 hover:bg-gray-100">
            <i data-lucide="radio" class="h-5 w-5"></i>
            <span class="font-medium">Live Board</span>
          </a>"""

for filepath in glob.glob(os.path.join(templates_dir, "*.html")):
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    
    modified = False
    if wrong_link_pattern.search(content):
        content = wrong_link_pattern.sub(correct_link, content)
        modified = True
        
    if modified:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"Updated {os.path.basename(filepath)}")
