# LaTeX output (only when profile.md asks for it)

Block A is then the complete resume in LaTeX, in one code block, ready to
paste into Overleaf, and no build is run. Save the same text to `resume.tex` in
the job folder. Add one line right after the code block: "If Overleaf shows a
red error, paste the first error message here and I'll return the full fixed
Block A." Before "apply", ask me to save the compiled PDF into the job folder
as `<First Last> Resume.pdf`. In Block B, Length is your estimate in pages,
since nothing was compiled.

- Start from the template below and keep its preamble and macros. Set the
  paper size (letterpaper or a4paper) from my preferences. To hit the target
  length you may adjust spacing, margins (not below 0.5in), and font size, but
  only 10pt or 11pt (the article class silently ignores other sizes such as
  10.5pt). If it still runs long at 10pt, cut the weakest bullets from older
  roles instead of cramming. Delete sections I have no material for. For
  students, recent graduates, or career changers whose strongest evidence is
  education or projects, put Education (and Projects) right after the
  Summary.
- It must compile without errors on Overleaf's default compiler (pdfLaTeX)
  using only the packages in the template.
- Escape these characters everywhere in text: & as \&, % as \%, $ as \$, # as
  \#, _ as \_, { as \{, } as \} (e.g. "40\%", "R\&D", "C\#"). A single
  unescaped % silently deletes the rest of its line, which is the most common
  way these resumes break. Never write a tilde (it prints as a space, so an
  "about $550K" written with a tilde loses its "about"); write "about". Don't
  use ^ or \ in text.
- Use only Latin letters (accented letters are fine), digits, ordinary
  punctuation, and the pound or euro sign. No greater-or-equal,
  less-or-equal or approximately-equal signs, Unicode minus signs, check
  marks, stars, emoji or icon characters, even if my files contain them:
  write "at least", "about", or a hyphen.
- Use -- for date ranges and \textbar{} between contact items. Replace the
  template's sample contact details with mine and delete any item I didn't
  give.
- Unless you actually compiled it, don't claim it compiles; give page length
  only as the estimate under Length in Block B.
- Added to the check under Output: escaping; every itemize has at least one
  \item (a role with no bullets has no itemize); \\ never right after
  \end{itemize} or a blank line; every \role has all four {...} groups; no
  Markdown formatting (**bold**, # headings).

```latex
\documentclass[letterpaper,11pt]{article}
\usepackage[T1]{fontenc}
\usepackage{lmodern}
\usepackage[margin=0.6in]{geometry}
\usepackage{titlesec}
\usepackage{enumitem}
\usepackage[hidelinks]{hyperref}
\ifdefined\pdfgentounicode      % makes the PDF text copy/paste- and ATS-clean
  \input{glyphtounicode}
  \pdfgentounicode=1
\fi
\pagestyle{empty}
\setlength{\parindent}{0pt}
\raggedright
\titleformat{\section}{\large\bfseries}{}{0pt}{}[\titlerule]
\titlespacing*{\section}{0pt}{10pt}{4pt}
\setlist[itemize]{leftmargin=1.2em, itemsep=1pt, topsep=2pt, parsep=0pt,
  beginpenalty=10000}
% \role{Title}{Dates}{Company}{Location}
\newcommand{\role}[4]{\par\addvspace{2pt}%
  \textbf{#1}\hfill #2\\*
  \textit{#3}\hfill\textit{#4}\par\nopagebreak}
% \project{Name}{Dates}, then an itemize of 1-3 bullets
\newcommand{\project}[2]{\par\addvspace{2pt}\textbf{#1}\hfill #2\par\nopagebreak}

\begin{document}

\begin{center}
  {\LARGE\bfseries First Last}\\[4pt]
  City, ST \textbar{} (555) 555-0100 \textbar{}
  \href{mailto:name@example.com}{name@example.com} \textbar{}
  \href{https://linkedin.com/in/yourname}{linkedin.com/in/yourname}
\end{center}

\section{Summary}
Two or three sentences.

\section{Experience}
\role{Job Title}{Mon YYYY -- Present}{Company}{City, ST}
\begin{itemize}
  \item Achievement bullet.
\end{itemize}
\vspace{4pt}

\section{Skills}
\textbf{Category:} Skill, Skill, Skill\\
\textbf{Category:} Skill, Skill, Skill

\section{Education}
\role{Degree, Field}{Mon YYYY}{Institution}{City, ST}
% \section{Projects}  % optional: \project{Name}{Mon YYYY} + itemize

\section{Certifications}
\begin{itemize}
  \item Certification Name
\end{itemize}

\end{document}
```
