# Universal delivery revision 6 (primary label claims and aligned plural bindings): all typed molecular references, strict coverage and image products.
from drugpaper.skills.molecule import mol_backfill as run

# Revision 7: preserve structural formula/SMILES metadata alongside each numbered reference.
# Revision 8: document-scoped explicit reference binding for ligands with no 2D numbered box.

__all__ = ["run"]

# Revision 9: typed ID-only compound instances, generic assembly and separate full-structure facts.

# Revision 10: label-derived single-center stereo and persisted instance references at route endpoints.

# Revision 11: modern CIP labels with final graph validation, including sulfur stereocenters.

# Revision 12: generic series/scaffold references never trigger concrete-label lookup.

# Revision 13: endpoint Markush explanations are descriptive alternatives, not concrete lookup/assembly inputs.

# Revision 14: enumerate product-only Markush candidates and assemble via explicit atom sites.
