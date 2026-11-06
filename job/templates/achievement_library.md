# Achievement library

Every achievement, role by role, written as a record: what was done, with every
number, qualifier and hedge. The /job skill reads this before tailoring a
resume and adds to it after each application. Edit it whenever you like; the
skill copies it to backups\ before every change it makes.

Format: one `### Job Title | Company | Mon YYYY - Mon YYYY` heading per role,
one achievement per bullet. Extra sections: `### Skills, Certifications &
Education` (including "Not held: ..." lines) and `### Projects, Labs &
Volunteer Work`.

Each entry starts with a stable ID, `- [L-0001] ...`, which resume bullets
cite as evidence. `library.py ids` assigns IDs to new entries in file order
and never renumbers or reuses one; the `<!-- last-id: ... -->` line at the end
keeps track. A corrected entry keeps its ID. "Not held" lines get no ID.
