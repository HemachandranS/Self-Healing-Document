import os
import json
from dotenv import load_dotenv
from groq import Groq
import subprocess

# Load environment variables
load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY not found in .env file!")

client = Groq(api_key=GROQ_API_KEY)


def pack_repo_context(project_root="."):
    """Pack file tree and existing .md docs into a single string."""
    context = "=== PROJECT FILE STRUCTURE ===\n"
    
    # 1. Gather file paths (skipping venv, git, bytecode)
    ignored = {".git", "__pycache__", ".venv", "venv", "node_modules", ".env"}
    for root, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in ignored]
        for file in files:
            rel_path = os.path.relpath(os.path.join(root, file), project_root)
            print(f"📄 Found file: {rel_path}")
            context += f"- {rel_path}\n"

    # 2. Append existing documentation files
    context += "\n=== EXISTING DOCUMENTATION (.md) ===\n"
    docs_dir = os.path.join(project_root, "docs")
    
    if os.path.exists(docs_dir):
        for file in os.listdir(docs_dir):
            if file.endswith(".md"):
                doc_path = os.path.join(docs_dir, file)
                with open(doc_path, "r", encoding="utf-8") as f:
                    context += f"\n--- FILE: docs/{file} ---\n{f.read()}\n"
    else:
        context += "No docs/ directory found.\n"

    return context

def get_git_diff_latest_commit():
    """Fetches the diff and commit message from the most recent commit (HEAD~1 to HEAD)."""
    try:
        # Get commit message / title
        pr_title = subprocess.check_output(
            ["git", "log", "-1", "--pretty=format:%s"],
            text=True
        ).strip()

        # Get diff of the latest commit
        diff_text = subprocess.check_output(
            ["git", "show", "HEAD"],
            text=True
        )

        return pr_title, diff_text
    except subprocess.CalledProcessError as e:
        print(f"Error fetching git commit diff: {e}")

def get_all_pr_commits_and_diff():
    """Extracts all commit messages and the combined diff for the current PR."""
    try:
        base_ref = os.getenv("GITHUB_BASE_REF", "main")
        
        # 1. Fetch the target base branch to compare against
        subprocess.run(["git", "fetch", "origin", base_ref], check=True)
        
        # 2. Get list of all commit messages included in this PR
        # Format: [commit hash] Commit Subject - Commit Body
        commit_logs = subprocess.check_output(
            ["git", "log", f"origin/{base_ref}..HEAD", "--pretty=format:- %h: %s %b"],
            text=True
        ).strip()
        
        # 3. Get the complete code diff across all commits in the PR
        git_diff = subprocess.check_output(
            ["git", "diff", f"origin/{base_ref}...HEAD"],
            text=True
        )

        pr_title = os.getenv("GITHUB_PR_TITLE", "Pull Request Changes")
        
        # Combine PR title with all individual commit logs
        full_context_message = f"PR Title: {pr_title}\n\nAll PR Commits:\n{commit_logs}"
        
        return full_context_message, git_diff

    except subprocess.CalledProcessError as e:
        print(f"Error extracting PR commits: {e}")
        return "PR Update", ""


def heal_documentation(git_diff, pr_title: str, target_doc_path: str):
    """Analyze code changes and update the target documentation file."""
    # Step A: Pack codebase context
    repo_context = pack_repo_context()

    # Step B: Read target doc content
    if os.path.exists(target_doc_path):
        with open(target_doc_path, "r", encoding="utf-8") as f:
            current_doc_text = f.read()
    else:
        current_doc_text = "# Functional Requirements\nNo document exists yet."

    # Step C: Prompt Groq openai/gpt-oss-120b
    system_prompt = """You are an AI Self-Healing Documentation Engine for a software project.
Your job is to analyze functional/business-logic changes in a git diff and update the Markdown documentation.

RULES:
1. Identify changes to business rules, system behavior, default values, or edge cases.
2. Update the functional explanations in the documentation to match the new code behavior.
3. DO NOT just write code syntax descriptions (avoid phrases like 'line 12 added an if statement').
4. Focus on functional impact (e.g., 'Cancellation policy changed from 24h to 48h prior to check-in').
5. Output ONLY valid JSON matching this schema:
{
  "functional_summary": "1-2 sentence summary of business rule changes",
  "updated_markdown": "Complete updated Markdown documentation text"
}"""

    user_prompt = f"""
=== HOTEL MANAGEMENT REPO CONTEXT ===
{repo_context}

=== TARGET DOCUMENTATION FILE ({target_doc_path}) ===
{current_doc_text}

=== CODE CHANGE (GIT DIFF) ===
PR Title: {pr_title}
commit title: {git_diff[0]}
Diff:
{git_diff[1]}

Generate the updated Markdown documentation in JSON format.
"""

    print("⚡ Sending request to Groq (openai/gpt-oss-120b)...")
    
    response = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        response_format={"type": "json_object"}
    )

    result = json.loads(response.choices[0].message.content)

    print("\n✅ --- FUNCTIONAL CHANGES DETECTED ---")
    print(result.get("functional_summary"))

    # Step D: Overwrite/Update the target doc
    os.makedirs(os.path.dirname(target_doc_path), exist_ok=True)
    with open(target_doc_path, "w", encoding="utf-8") as f:
        f.write(result.get("updated_markdown"))

    print(f"\n🎉 Successfully healed document: {target_doc_path}")


if __name__ == "__main__":
    # Test Run with a sample Hotel Management diff
    sample_pr_title = "feat(booking): Update cancellation rules and add late checkout fee"
    
    sample_diff = get_all_pr_commits_and_diff()

    print(sample_diff)

    # Target doc file to self-heal
    doc_file = "docs/booking_policy.md"

    heal_documentation(
        git_diff=sample_diff,
        pr_title=sample_pr_title,
        target_doc_path=doc_file
    )