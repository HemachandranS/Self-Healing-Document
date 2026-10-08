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

def get_all_pr_commits_and_diffs(base_branch="main"):
    """
    Fetches all commits from the current feature branch relative to base_branch.
    Returns a dictionary where:
        Key = Commit title (%s)
        Value = Commit diff
    """
    commit_dict = {}
    
    try:
        # 1. Get the list of commit hashes between base_branch and current HEAD
        # %H returns full commit hashes in reverse chronological order (newest first)
        hashes_output = subprocess.check_output(
            ["git", "log", f"{base_branch}..HEAD", "--pretty=format:%H"],
            text=True
        ).strip()

        if not hashes_output:
            print(f"No commits found between {base_branch} and HEAD.")
            return commit_dict

        commit_hashes = hashes_output.splitlines()

        # 2. Iterate through each commit hash
        for commit_hash in commit_hashes:
            # Fetch the commit message / title
            commit_title = subprocess.check_output(
                ["git", "log", "-1", "--pretty=format:%s", commit_hash],
                text=True
            ).strip()

            # Fetch the specific diff for this commit
            commit_diff = subprocess.check_output(
                ["git", "show", commit_hash],
                text=True
            )

            # Assign to dictionary
            commit_dict[commit_title] = commit_diff

        return commit_dict

    except subprocess.CalledProcessError as e:
        print(f"Error fetching git commit diffs: {e}")
        return commit_dict


def heal_documentation(commit_dict: dict, cumulative_diff: str, target_doc_path: str, pr_title: str = ""):
    """
    Analyzes the dictionary of commit titles and diffs to update functional documentation.
    
    Args:
        commit_dict: Dict mapping { "Commit Title": "Commit Diff Text" }
        cumulative_diff: String containing full diff across all PR commits
        target_doc_path: Path to the markdown documentation file to update
        pr_title: Optional title/summary of the PR
    """
    # Step A: Pack codebase context
    repo_context = pack_repo_context()

    # Step B: Read target document content
    if os.path.exists(target_doc_path):
        with open(target_doc_path, "r", encoding="utf-8") as f:
            current_doc_text = f.read()
    else:
        current_doc_text = "# Functional Requirements\nNo document exists yet."

    # Step C: Format commit dictionary into structured prompt context
    formatted_commits_text = ""
    for idx, (title, diff_text) in enumerate(commit_dict.items(), 1):
        formatted_commits_text += f"\n--- COMMIT #{idx}: {title} ---\n{diff_text}\n"

    # Step D: System and User Prompts
    system_prompt = """You are an AI Self-Healing Documentation Engine for a software repository.
Your goal is to analyze functional and business-logic changes across PR commits and update the Markdown documentation.

RULES:
1. Understand the developer's intent from the commit messages and code changes across all PR commits.
2. Update functional rules, system behavior, business logic, default values, or edge cases in the documentation.
3. DO NOT write line-by-line syntax commentary (avoid 'added an if statement on line 10'). Focus on functional impact.
4. Output ONLY valid JSON with this exact structure:
{
  "functional_summary": "1-2 sentence executive summary of business/functional rule changes",
  "updated_markdown": "Complete updated Markdown documentation text"
}"""

    user_prompt = f"""
=== HOTEL MANAGEMENT REPO CONTEXT ===
{repo_context}

=== TARGET DOCUMENTATION FILE ({target_doc_path}) ===
{current_doc_text}

=== PR OVERVIEW ===
PR Title: {pr_title if pr_title else 'Pull Request Updates'}
Total Commits Analyzed: {len(commit_dict)}

=== INDIVIDUAL COMMITS (DEVELOPER INTENT & DIFFS) ===
{formatted_commits_text}

=== FULL CUMULATIVE PR CODE DIFF ===
{cumulative_diff}

Generate the updated Markdown documentation in JSON format.
"""

    print(f"⚡ Sending {len(commit_dict)} commit(s) to Groq (openai/gpt-oss-120b)...")

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

    # Step E: Overwrite/Update the documentation file
    os.makedirs(os.path.dirname(target_doc_path), exist_ok=True)
    with open(target_doc_path, "w", encoding="utf-8") as f:
        f.write(result.get("updated_markdown"))

    print(f"\n🎉 Successfully healed document: {target_doc_path}")

if __name__ == "__main__":
    # Test Run with a sample Hotel Management diff
    sample_pr_title = "feat(booking): Update cancellation rules and add late checkout fee"
    
    sample_diff = get_all_pr_commits_and_diffs()

    print(sample_diff)

    # Target doc file to self-heal
    doc_file = "docs/booking_policy.md"

    heal_documentation(
        commit_dict=sample_diff,
        pr_title=sample_pr_title,
        target_doc_path=doc_file
    )