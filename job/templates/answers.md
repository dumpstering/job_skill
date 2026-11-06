# Screening answers

Answers to application questions that come up again and again, one bank for
every application. The /job skill fills forms from this file and adds to it
when you answer a question that will recur. Edit it whenever you like; the
skill copies it to backups\ before every change it makes.

It never holds answers about criminal history, attestations, consent boxes,
or ID numbers: those are always yours to answer on the form.

One answer per line, in this form (answers.py reads only lines that start
with "- ANS-"):

    - ANS-001 | <question> | <answer> | <kind> | <scope> | <sensitive> | <source> | <last confirmed> | also: <other wording>; <other wording>

- kind: text, yesno, number or choice.
- scope: any, US or Canada (the country it applies to). Write Canada, not
  CA, which reads as California; an older line that says CA is still read
  as Canada.
- sensitive: yes for salary, visa or immigration status, and anything you
  want confirmed before it is used; each job's sensitive answers are listed
  in the morning card for your ok.
- source: where the answer comes from: profile:<field>, library:L-0031,
  computed:years(<technique>), template:<family> (a short approved answer
  with {company} and {role} filled from the posting), or user:YYYY-MM-DD.
- also: other wordings of the same question (optional).
