"""Portable identity-priority regressions; no Django server or Ollama needed."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from comparator.services.correspondence import find_correspondences
from comparator.services.deterministic import extract_type_names


class IdentityPriorityTests(unittest.TestCase):
    def compare(self, left, right, **settings):
        with tempfile.TemporaryDirectory() as root:
            for side, files in [('left', left), ('right', right)]:
                (Path(root) / side).mkdir()
                for name, content in files.items():
                    path = Path(root) / side / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(content if isinstance(content, bytes) else content.encode())
            with patch('comparator.services.correspondence.is_ollama_available', return_value=False), \
                    patch('comparator.services.correspondence.compare_with_llm') as llm:
                result = find_correspondences(
                    str(Path(root) / 'left'), str(Path(root) / 'right'),
                    settings={'max_llm_per_file': 0, **settings},
                )
                llm.assert_not_called()
            self.assertEqual(len({m.left_file.full_path for m in result.matched}), len(result.matched))
            self.assertEqual(len({m.right_file.full_path for m in result.matched}), len(result.matched))
            return result

    def test_rewritten_same_class_beats_renamed_structural_copy(self):
        original = 'public class Invoice { public int total() { return 1; } }'
        rewritten = 'public class Invoice {\n' + '\n'.join(
            f'private String field{i}; public void update{i}() {{ field{i} = null; }}'
            for i in range(100)) + '\n}'
        result = self.compare({'old/Invoice.java': original}, {
            'new/Invoice.java': rewritten,
            'new/InvoiceCopy.java': original.replace('Invoice', 'InvoiceCopy'),
        })
        self.assertEqual(len(result.matched), 1)
        match = result.matched[0]
        self.assertEqual(match.right_file.filename, 'Invoice.java')
        self.assertEqual(match.content_status, 'different')
        self.assertLess(match.similarity, 40)

    def test_identical_duplicate_reserved_before_edited_duplicate(self):
        original = 'public class Invoice { int amount = 1; }'
        edited = 'public class Invoice { int amount = 2; }'
        for left in [
            {'a/Invoice.java': edited, 'z/Invoice.java': original},
            {'z/Invoice.java': original, 'a/Invoice.java': edited},
        ]:
            result = self.compare(left, {'target/Invoice.java': original})
            self.assertEqual(result.matched[0].left_file.relative_path, 'z/Invoice.java')
            self.assertEqual(result.matched[0].content_status, 'identical')

    def test_true_identity_beats_normalized_string_identity(self):
        original = 'public class Invoice { String name = "old"; }'
        result = self.compare({'old/Invoice.java': original}, {
            'a/Invoice.java': original.replace('"old"', '"new"'),
            'z/Invoice.java': original,
        })
        self.assertEqual(result.matched[0].right_file.relative_dir, 'z')
        self.assertEqual(result.matched[0].content_status, 'identical')

    def test_type_identity_survives_extension_change(self):
        result = self.compare({'old/invoice.py': 'class Invoice:\n    pass\n'}, {
            'new/invoice.custom': 'class Invoice:\n' + '\n'.join(
                f'    def method{i}(self): return {i}' for i in range(80)),
        })
        self.assertEqual(len(result.matched), 1)

    def test_comments_and_strings_are_not_types(self):
        self.assertEqual(extract_type_names(
            '// class Fake {}\n/* class AlsoFake {} */\n'
            'String text = "class Example {}";\npublic class Real {}', '.java'), {'Real'})
        self.assertEqual(extract_type_names(
            '"""class Example: pass"""\n# class Fake\nclass Real: pass', '.py'), {'Real'})

    def test_same_filename_alone_does_not_force_unrelated_match(self):
        result = self.compare({'old/data.txt': 'aaaa aaaa aaaa'}, {
            'new/data.txt': 'zzzz zzzz zzzz',
        }, content_sim_threshold=100)
        self.assertFalse(result.matched)

    def test_binary_isolation(self):
        result = self.compare({'old/Invoice.bin': b'\x00class Invoice {}'}, {
            'new/Invoice.java': 'class Invoice {}',
        })
        self.assertFalse(result.matched)

    def test_exact_path_retains_priority(self):
        result = self.compare({'Invoice.java': 'class Invoice {}'}, {
            'Invoice.java': 'class Replacement {}',
            'moved/Invoice.java': 'class Invoice {}',
        })
        self.assertEqual(result.matched[0].match_type, 'exact_path')
        self.assertEqual(result.matched[0].right_file.relative_dir, '')


if __name__ == '__main__':
    unittest.main()
