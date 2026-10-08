import os
import json
import subprocess
from dotenv import load_dotenv
from groq import Groq

# Load environment variables
load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY not found in .env file!")

client = Groq(api_key=GROQ_API_KEY)


def get_all_pr_commits_and_diffs(base_branch="self_healing_doccumentation"):
    """
    Fetches all commits from the feature branch relative to base_branch using git log.
    Returns:
        commit_dict: { "Commit Message/Title": "Commit Diff Text", ... }
        cumulative_diff: Full combined git diff across all PR commits
    """
    commit_dict = {}
    cumulative_diff = ""

    try:
        # Safeguard: Ensure base branch reference exists locally (especially in CI)
        if os.getenv("GITHUB_ACTIONS") == "true":
            base_ref = os.getenv("GITHUB_BASE_REF", base_branch)
            subprocess.run(["git", "fetch", "origin", f"{base_ref}:{base_ref}"], check=False)
            base_branch = base_ref

        # 1. Get commit hashes from base_branch to HEAD
        hashes_output = subprocess.check_output(
            ["git", "log", f"{base_branch}..new_brranch", "--pretty=format:%H"],
            text=True
        ).strip()

        if hashes_output:
            commit_hashes = hashes_output.splitlines()

            # Iterate through all commit hashes
            for commit_hash in commit_hashes:
                # Get commit title / message
                commit_title = subprocess.check_output(
                    ["git", "log", "-1", "--pretty=format:%s", commit_hash],
                    text=True
                ).strip()

                # Get diff for this specific commit
                commit_diff = subprocess.check_output(
                    ["git", "show", commit_hash],
                    text=True
                )

                commit_dict[commit_title] = commit_diff

            # 2. Fetch total cumulative diff across the entire PR
            cumulative_diff = subprocess.check_output(
                ["git", "diff", f"{base_branch}...HEAD"],
                text=True
            )
        else:
            # Local fallback if no branch difference is found (e.g. uncommitted/latest local commit)
            print("⚠️ No commits found relative to base branch. Checking local changes...")
            commit_title = subprocess.check_output(
                ["git", "log", "-1", "--pretty=format:%s"], text=True
            ).strip()
            commit_diff = subprocess.check_output(["git", "show", "HEAD"], text=True)
            commit_dict[commit_title] = commit_diff
            cumulative_diff = commit_diff

    except subprocess.CalledProcessError as e:
        print(f"⚠️ Git subprocess error: {e}")

    return commit_dict, cumulative_diff


def pack_repo_context(project_root="."):
    """Pack file tree and existing .md docs into a single string context."""
    context = "=== PROJECT FILE STRUCTURE ===\n"
    ignored = {".git", "__pycache__", ".venv", "venv", "node_modules", ".env"}

    for root, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in ignored]
        for file in files:
            rel_path = os.path.relpath(os.path.join(root, file), project_root)
            context += f"- {rel_path}\n"

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
    # Fetch commit dict and cumulative diff for the feature branch
    pr_commits, full_diff = get_all_pr_commits_and_diffs(base_branch="self_healing_doccumentation")

    if pr_commits:
        doc_file = "docs/booking_policy.md"
        pr_title_env = os.getenv("GITHUB_PR_TITLE", "PR Updates")

        heal_documentation(
            commit_dict=pr_commits,
            cumulative_diff=full_diff,
            target_doc_path=doc_file,
            pr_title=pr_title_env
        )
    else:
        print("No commit differences detected between feature branch and base branch.")