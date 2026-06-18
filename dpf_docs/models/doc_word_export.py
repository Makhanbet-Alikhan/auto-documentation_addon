# -*- coding: utf-8 -*-
"""Word (.docx) export for documented modules, user-manual style.

Generates a complete Russian-language user manual in Word format:

* Title page (system name, "Руководство пользователя", version, developer, city/year)
* Real Word TOC field ("ОГЛАВЛЕНИЕ")
* 1. Введение (1.1 Категории, 1.2 Область, 1.3 Назначение, 1.4 Соглашения)
* 2. Содержание документа (2.1 Назначение, 2.2 Материалы, 2.3 Подготовка)
* 3. Список функций — one block per function:
    source='auto'    -> full render: Описание / Требования / Порядок / screenshot / Результат
                        If a source='project' function follows immediately and has
                        a description, that text is appended to the Описание paragraph
                        of the auto function so the context stays together.
    source='project' -> if no preceding auto function absorbed it, rendered as a
                        short italic note (no heading, no bold label) to avoid
                        polluting the document structure.
* 4. Литература
* 5. Словарь терминов

Function and figure numbers are assigned by a single sequential counter
that spans all functions regardless of source, guaranteeing correct
сквозной numeration across the whole document.

``python-docx`` is loaded lazily so the addon still installs without it;
the Word button then raises a clear, actionable error.
"""
import base64
import io
import logging

from odoo import _, api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

try:
    import docx  # python-docx
    from docx.shared import Inches, Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    HAS_DOCX = True
except ImportError:  # pragma: no cover
    HAS_DOCX = False

BODY_FONT = "Times New Roman"
BODY_SIZE = 12


def _grey():
    return RGBColor(0x80, 0x80, 0x80)


def _red():
    return RGBColor(0xC0, 0x00, 0x00)


class DocWordExport(models.AbstractModel):
    _name = "doc.word.export"
    _description = "DPF Docs - Word Export Service"

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    @api.model
    def build_docx(self, doc_modules):
        """Return raw .docx bytes for all given doc.module records."""
        if not HAS_DOCX:
            raise UserError(_(
                "Пакет 'python-docx' не установлен на сервере.\n"
                "Попросите администратора выполнить:  pip install python-docx"
            ))

        # Re-number all functions before export so DB values match the document.
        self._renumber_functions(doc_modules)

        document = docx.Document()
        self._apply_base_styles(document)

        primary = doc_modules[:1]
        self._add_cover(document, primary)
        self._add_toc(document)
        self._add_intro_section(document, primary)
        self._add_content_section(document, primary)
        self._add_functions_section(document, doc_modules)
        self._add_bibliography_section(document, primary)
        self._add_glossary_section(document, primary)

        buf = io.BytesIO()
        document.save(buf)
        return buf.getvalue()

    # ------------------------------------------------------------------
    # Function renumbering
    # ------------------------------------------------------------------
    @api.model
    def _renumber_functions(self, doc_modules):
        """
        Assign sequential 1-based numbers to all functions across all modules.

        Functions are sorted per module by (sequence, id) and numbered
        globally so every Функция N has a unique N.
        """
        counter = 0
        for doc_module in doc_modules:
            funcs = doc_module.function_ids.sorted(
                key=lambda f: ((f.sequence or 999999), f.id)
            )
            for func in funcs:
                counter += 1
                if func.number != counter:
                    func.write({'number': counter})

    # ------------------------------------------------------------------
    # Styling
    # ------------------------------------------------------------------
    def _apply_base_styles(self, document):
        style = document.styles["Normal"]
        style.font.name = BODY_FONT
        style.font.size = Pt(BODY_SIZE)
        rpr = style.element.get_or_add_rPr()
        rfonts = rpr.get_or_add_rFonts()
        rfonts.set(qn("w:cs"), BODY_FONT)
        rfonts.set(qn("w:eastAsia"), BODY_FONT)

    # ------------------------------------------------------------------
    # Cover page
    # ------------------------------------------------------------------
    def _add_cover(self, document, modules):
        m = modules[:1]
        system_name = (m.system_name if m else None) or (
            'Система "%s"' % (m.name if m else "Odoo")
        )
        platform = (m.platform_version if m else None) or "Odoo 19"
        version = (m.manual_version if m else None) or "1.0"
        developer = (m.developer if m else None) or ""
        city_year = (m.city_year if m else None) or ""

        for _ in range(7):
            document.add_paragraph()

        self._centered(document, system_name, size=18)
        self._centered(document, 'на базе платформы "%s"' % platform, size=16)
        document.add_paragraph()
        self._centered(document, "Руководство пользователя", bold=True, size=18)
        self._centered(document, "Версия %s" % version, size=14)

        for _ in range(4):
            document.add_paragraph()

        if developer:
            self._centered(document, "Разработчик: %s" % developer, size=12)

        for _ in range(6):
            document.add_paragraph()

        if city_year:
            self._centered(document, city_year, size=12)

        document.add_page_break()

    def _centered(self, document, text, bold=False, size=12):
        p = document.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = p.add_run(text)
        r.bold = bold
        r.font.size = Pt(size)
        r.font.name = BODY_FONT
        return p

    # ------------------------------------------------------------------
    # Table of contents
    # ------------------------------------------------------------------
    def _add_toc(self, document):
        h = document.add_paragraph()
        h.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = h.add_run("ОГЛАВЛЕНИЕ")
        run.bold = True
        run.font.size = Pt(14)
        document.add_paragraph()

        p = document.add_paragraph()
        r = p.add_run()
        fld_begin = OxmlElement("w:fldChar")
        fld_begin.set(qn("w:fldCharType"), "begin")
        instr = OxmlElement("w:instrText")
        instr.set(qn("xml:space"), "preserve")
        instr.text = r'TOC \o "1-3" \h \z \u'
        fld_sep = OxmlElement("w:fldChar")
        fld_sep.set(qn("w:fldCharType"), "separate")
        hint = OxmlElement("w:t")
        hint.text = (
            "Оглавление обновляется автоматически: "
            "правый клик → «Обновить поле»."
        )
        fld_end = OxmlElement("w:fldChar")
        fld_end.set(qn("w:fldCharType"), "end")
        r._r.extend([fld_begin, instr, fld_sep, hint, fld_end])
        document.add_page_break()

    # ------------------------------------------------------------------
    # Heading helpers
    # ------------------------------------------------------------------
    def _heading(self, document, text, level):
        p = document.add_heading("", level=level)
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        r = p.add_run(text)
        r.bold = True
        r.font.color.rgb = RGBColor(0, 0, 0)
        r.font.name = BODY_FONT
        r.font.size = Pt(14 if level == 1 else 12)
        return p

    def _paragraph(self, document, text):
        if text and text.strip():
            document.add_paragraph(text.strip())

    def _bullets(self, document, text):
        """Each non-empty line becomes a bullet."""
        for line in (text or "").splitlines():
            line = line.strip()
            if line:
                document.add_paragraph(line, style="List Bullet")

    def _numbered_lines(self, document, text):
        """Each non-empty line becomes a numbered paragraph starting at 1."""
        for i, line in enumerate(
            [l.strip() for l in (text or "").splitlines() if l.strip()], 1
        ):
            p = document.add_paragraph()
            p.paragraph_format.left_indent = Pt(18)
            p.add_run("%d. %s" % (i, line))

    # ------------------------------------------------------------------
    # 1. Введение
    # ------------------------------------------------------------------
    def _add_intro_section(self, document, modules):
        m = modules[:1]
        self._heading(document, "1. Введение", 1)

        self._heading(document, "1.1. Описание категорий пользователей", 2)
        self._bullets(document, m.intro_user_categories if m else "")

        self._heading(document, "1.2. Область применения", 2)
        self._bullets(document, m.intro_scope if m else "")

        self._heading(document, "1.3. Назначение документа", 2)
        self._paragraph(document, m.intro_purpose if m else "")

        self._heading(document, "1.4. Соглашения", 2)
        self._bullets(document, m.intro_conventions if m else "")

    # ------------------------------------------------------------------
    # 2. Содержание документа
    # ------------------------------------------------------------------
    def _add_content_section(self, document, modules):
        m = modules[:1]
        self._heading(document, "2. Содержание документа", 1)

        self._heading(document, "2.1. Назначение", 2)
        self._paragraph(document, m.content_purpose if m else "")

        self._heading(document, "2.2. Необходимые материалы", 2)
        self._bullets(document, m.content_materials if m else "")

        self._heading(document, "2.3. Подготовка к работе", 2)
        self._numbered_lines(document, m.content_preparation if m else "")

    # ------------------------------------------------------------------
    # 3. Список функций
    # ------------------------------------------------------------------
    def _add_functions_section(self, document, doc_modules):
        self._heading(document, "3. Список функций", 1)

        # Global counters span all modules so numeration is always sequential.
        figure_counter = [0]  # mutable container so helpers can update it
        func_counter = [0]

        sub = 0
        for doc_module in doc_modules:
            sub += 1
            mod_name = doc_module.name or doc_module.technical_name
            self._heading(document, "3.%d. %s" % (sub, mod_name), 2)

            funcs = list(doc_module.function_ids.sorted(
                key=lambda f: ((f.sequence or 999999), f.id)
            ))
            if not funcs:
                p = document.add_paragraph()
                r = p.add_run("Функции для данного модуля не сформированы.")
                r.font.color.rgb = _grey()
                r.italic = True
                continue

            # ----------------------------------------------------------
            # Pre-pass: group project descriptions into their predecessor
            # auto function so they render inline instead of standalone.
            #
            # Strategy:
            #   - Walk the sorted list once.
            #   - When we hit a source='project' func, look back for the
            #     most recent source='auto' func in the same module and
            #     attach the description there.
            #   - If no auto predecessor exists, keep it in the list for
            #     standalone rendering (as a compact italic note).
            # ----------------------------------------------------------
            # Map: auto_func.id -> list of project descriptions to append
            extra_descs = {}   # {auto_func_id: [str, ...]}
            orphan_projects = []  # project funcs with no auto predecessor
            last_auto = None

            for func in funcs:
                src = (getattr(func, 'source', 'auto') or 'auto')
                if src != 'project':
                    last_auto = func
                else:
                    desc = (func.description or '').strip()
                    if not desc:
                        continue  # nothing to show, skip silently
                    if last_auto is not None:
                        extra_descs.setdefault(last_auto.id, []).append(desc)
                    else:
                        orphan_projects.append(func)

            # Render auto functions (with inline project descriptions)
            for func in funcs:
                src = (getattr(func, 'source', 'auto') or 'auto')
                if src == 'project':
                    continue  # handled inline or as orphan below
                func_counter[0] += 1
                appended = extra_descs.get(func.id, [])
                self._add_function(
                    document, func,
                    func_num=func_counter[0],
                    figure_counter=figure_counter,
                    extra_descriptions=appended,
                )

            # Render orphan project functions (no auto predecessor)
            for func in orphan_projects:
                self._add_orphan_project_function(document, func)

    def _add_function(self, document, func, func_num, figure_counter,
                      extra_descriptions=None):
        """
        Render one auto function block.

        Parameters
        ----------
        func_num           : sequential 1-based function number
        figure_counter     : list([int]) — mutable counter, updated in-place
        extra_descriptions : list of str — project task descriptions to append
                             to the Описание paragraph of this function
        """
        extra_descriptions = extra_descriptions or []

        # --- Функция N: Title ---
        title_p = document.add_paragraph()
        title_p.paragraph_format.space_before = Pt(10)
        r = title_p.add_run("Функция %d: %s." % (func_num, func.name or ""))
        r.bold = True
        r.font.size = Pt(12)

        # --- Описание (auto text + any project enrichments inline) ---
        desc_parts = []
        if func.description and func.description.strip():
            desc_parts.append(func.description.strip())
        for extra in extra_descriptions:
            if extra:
                desc_parts.append(extra)

        if desc_parts:
            p = document.add_paragraph()
            lbl = p.add_run("Описание: ")
            lbl.bold = True
            p.add_run("\n".join(desc_parts))

        # --- Требования (red text) ---
        if func.requirements and func.requirements.strip():
            p = document.add_paragraph()
            lbl = p.add_run("Требования: ")
            lbl.bold = True
            val = p.add_run(func.requirements.strip())
            val.font.color.rgb = _red()

        # --- Порядок выполнения ---
        steps = func.step_lines() if hasattr(func, "step_lines") else []
        if steps:
            p = document.add_paragraph()
            p.add_run("Порядок выполнения:").bold = True
            for idx, step in enumerate(steps, 1):
                sp = document.add_paragraph()
                sp.paragraph_format.left_indent = Pt(18)
                sp.add_run("%d. %s" % (idx, step))

        # --- Screenshot or placeholder ---
        if func.screenshot:
            figure_counter[0] = self._embed_screenshot(
                document, func, figure_counter[0]
            )
        else:
            figure_counter[0] = self._add_screenshot_placeholder(
                document, func, figure_counter[0]
            )

        # --- Результат ---
        self._labelled(document, "Результат:", func.result)

        # Separator
        sep = document.add_paragraph()
        sep.paragraph_format.space_after = Pt(6)

    def _add_orphan_project_function(self, document, func):
        """
        Render a project function that had no auto predecessor to attach to.

        Uses a compact italic paragraph — no function heading, no screenshot
        placeholder — so it doesn't inflate the document structure.
        """
        desc = (func.description or '').strip()
        if not desc:
            return
        p = document.add_paragraph()
        r = p.add_run("%s: %s" % (func.name or "Дополнение", desc))
        r.italic = True
        r.font.color.rgb = _grey()
        r.font.size = Pt(11)
        sep = document.add_paragraph()
        sep.paragraph_format.space_after = Pt(4)

    def _labelled(self, document, label, value):
        """Bold label + normal text in the same paragraph."""
        if not value or not value.strip():
            return
        p = document.add_paragraph()
        r = p.add_run(label + " ")
        r.bold = True
        p.add_run(value.strip())

    def _embed_screenshot(self, document, func, figure):
        """Embed a real screenshot image with a figure caption. Returns updated figure count."""
        try:
            stream = io.BytesIO(base64.b64decode(func.screenshot))
            document.add_picture(stream, width=Inches(5.5))
            document.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
            figure += 1
            cap_p = document.add_paragraph()
            cap_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            caption_text = func.screenshot_caption or func.name or ""
            cap_r = cap_p.add_run("Рис.%d %s" % (figure, caption_text))
            cap_r.font.size = Pt(10)
            cap_r.italic = True
        except Exception as exc:  # pragma: no cover
            _logger.warning(
                "Could not embed screenshot for function %s: %s", func.id, exc
            )
        return figure

    def _add_screenshot_placeholder(self, document, func, figure):
        """Вставить текстовую заглушку вместо скриншота. Returns updated figure count."""
        figure += 1
        name = func.name or "экрана"
        caption_text = func.screenshot_caption or name

        p = document.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = p.add_run(
            "📌 [Здесь должен быть скриншот экрана «%s»]"
            % name
        )
        r.font.color.rgb = _grey()
        r.italic = True
        r.font.size = Pt(11)

        cap_p = document.add_paragraph()
        cap_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        cap_r = cap_p.add_run("Рис.%d %s" % (figure, caption_text))
        cap_r.font.size = Pt(10)
        cap_r.italic = True
        cap_r.font.color.rgb = _grey()

        return figure

    # ------------------------------------------------------------------
    # 4. Литература / 5. Словарь
    # ------------------------------------------------------------------
    def _add_bibliography_section(self, document, modules):
        m = modules[:1]
        document.add_page_break()
        self._heading(document, "4. Литература", 1)
        self._bullets(document, m.bibliography if m else "")

    def _add_glossary_section(self, document, modules):
        m = modules[:1]
        self._heading(document, "5. Словарь терминов", 1)
        self._bullets(document, m.glossary if m else "")
