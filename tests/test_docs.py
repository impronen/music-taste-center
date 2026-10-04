"""The README and docs are the front door: their relative links, images and anchors must resolve."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)\)")


def slug(heading: str) -> str:
    """GitHub's anchor for a heading: lower case, punctuation dropped, spaces to hyphens."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def anchors(path: Path) -> set[str]:
    """GitHub's heading anchors, ignoring `# comment` lines inside code fences and numbering repeats (-1, -2)."""
    found: dict[str, int] = {}
    fenced = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif not fenced and (m := re.match(r"^#{1,6}\s+(.+?)\s*$", line)):
            base = slug(m.group(1))
            n = found.get(base, -1) + 1
            found[base] = n
            if n:
                found[f"{base}-{n}"] = 0
    return set(found)


class DocsTests(unittest.TestCase):
    def test_relative_links_images_and_anchors_resolve(self):
        problems = []
        for doc in DOCS:
            for target in LINK.findall(doc.read_text(encoding="utf-8")):
                if target.startswith(("http://", "https://", "mailto:")):
                    continue
                path, _, fragment = target.partition("#")
                dest = (doc.parent / path).resolve() if path else doc
                if not dest.exists():
                    problems.append(f"{doc.name}: {target} does not exist")
                elif fragment and dest.suffix == ".md" and fragment not in anchors(dest):
                    problems.append(f"{doc.name}: {target} has no such heading")
        self.assertEqual(problems, [])

    def test_a_comment_inside_a_code_fence_is_not_a_heading(self):
        text = "# Real\n\n```sh\n# once: do this\n```\n"
        with __import__("tempfile").TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.md"
            path.write_text(text, encoding="utf-8")
            self.assertEqual(anchors(path), {"real"})

    def test_repeated_headings_get_github_numbering(self):
        with __import__("tempfile").TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.md"
            path.write_text("# Notes\n\n## Notes\n", encoding="utf-8")
            self.assertEqual(anchors(path), {"notes", "notes-1"})

    def test_the_readme_does_not_name_a_person_or_a_private_data_path(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        for needle in ("/Users/", "C:\\Users", "@gmail", "@icloud"):
            self.assertNotIn(needle, text)

    def test_screenshots_are_small_enough_to_keep_the_repo_light(self):
        for image in (ROOT / "docs" / "images").glob("*"):
            self.assertLess(image.stat().st_size, 400_000, image.name)


if __name__ == "__main__":
    unittest.main()
