import os

output = ""
for filename in os.listdir("."):
    if filename.endswith(".py"):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                output += f"### file: {filename}\n{f.read()}\n\n"
        except Exception as e:
            output += f"### file: {filename}\n[Could not read: {e}]\n\n"

with open("codebase_dump.txt", "w", encoding="utf-8") as f:
    f.write(output)

print("Done!")