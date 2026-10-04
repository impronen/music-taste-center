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
    return {slug(m.group(1)) for m in re.finditer(r"^#{1,6}\s+(.+?)\s*$", path.read_text(encoding="utf-8"), re.M)}


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

    def test_the_readme_does_not_name_a_person_or_a_private_data_path(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        for needle in ("/Users/", "C:\\Users", "@gmail", "@icloud"):
            self.assertNotIn(needle, text)

    def test_screenshots_are_small_enough_to_keep_the_repo_light(self):
        for image in (ROOT / "docs" / "images").glob("*"):
            self.assertLess(image.stat().st_size, 400_000, image.name)


if __name__ == "__main__":
    unittest.main()
