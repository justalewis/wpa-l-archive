# Builds the WPA-L icon sprite and a preview sheet.
#
#   python tools/icons/build_icons.py      (from the repo root)
#
# Writes templates/_icons_wpa.html (the sprite partial), wpal/icons.py (the
# index the /design page lists), and, when cairosvg is installed, the two
# preview sheets beside this file.
#
# The accent styles below are the only inline styles on the site. The CSP
# allows exactly these two strings by hash ('unsafe-hashes'); a new accent
# style would need its hash added in wpal/app.py, so keep to these two.
import hashlib
import base64
import pathlib

try:
    import cairosvg
except ImportError:  # previews are optional; the sprite is not
    cairosvg = None

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
A = 'style="stroke:var(--ic-accent)"'
AF = 'style="fill:var(--ic-accent);stroke:none"'
ICONS = [
 ("ic-wpa","Wordmark","The reply mark: a quoted line, then the cursor waiting for your answer.",
  f'<path {A} d="M6 7l6 5-6 5"/><path d="M13.5 17h5"/>'),
 ("ic-browse","Browse years","The stacks: volumes on a shelf, one year per spine.",
  f'<rect x="4" y="7" width="4" height="13"/><rect x="9" y="4.5" width="4.5" height="15.5"/><path d="M15 8.5l3.6-1 3 11.6"/><path d="M3 20.5h18"/><path {A} d="M9 8h4.5"/>'),
 ("ic-month","A month","The syllabus calendar: weeks in rows, one marked.",
  f'<rect x="4.5" y="5" width="15" height="15.5" rx="1"/><path d="M8.5 3.5v3M15.5 3.5v3M4.5 9h15M7.5 12.5h9M7.5 16h5"/><circle {AF} cx="15.8" cy="16" r="1.3"/>'),
 ("ic-thread","Thread","The quoted reply chain: a post, the chevron, the answer indented below.",
  f'<path d="M4 5.5h16"/><path {A} d="M4.5 9.5l2.4 2-2.4 2"/><path d="M10 11.5h10M10 15.5h10M10 19.5h6"/>'),
 ("ic-message","Message","The letter in its envelope; the flap is the opening.",
  f'<rect x="3.5" y="6" width="17" height="12.5" rx="1"/><path {A} d="M3.5 7l8.5 6.2L20.5 7"/>'),
 ("ic-search","Search","The research index card under the lens.",
  f'<rect x="3.5" y="4.5" width="12" height="9" rx=".5"/><path d="M6 7.5h7M6 10.5h3.5"/><circle {A} cx="15.5" cy="15" r="3.5"/><path d="M18 17.5l2.8 2.8"/>'),
 ("ic-timeline","Timeline","The classroom ruler, with the term marked.",
  f'<rect x="3" y="9.5" width="18" height="6" rx=".5"/><path d="M6 9.5V12M9 9.5v1.5M12 9.5V12M15 9.5v1.5M18 9.5V12"/><path {A} d="M13.6 4.5h2.8L15 7z"/>'),
 ("ic-trends","Term trends","Graph paper and a pencil line rising and falling.",
  f'<path d="M4 4v16h16"/><path {A} d="M7 15.5l3.2-3.4 3 2.2 4.6-6.3"/>'),
 ("ic-person","Author profile","Head and shoulders above a byline.",
  f'<circle cx="12" cy="8.5" r="3.5"/><path d="M5.5 18c.8-3.3 3.3-5 6.5-5s5.7 1.7 6.5 5"/><path {A} d="M8 21h8"/>'),
 ("ic-data","Data and export","The portfolio folder, with the corpus going in to be carried away.",
  f'<path d="M3.5 7.5v11.5h17v-9.5h-8l-2-2z"/><path {A} d="M12 11v5.5M9.8 14.3l2.2 2.2 2.2-2.2"/>'),
 ("ic-confidence","Thread confidence","Two leaves and the stitching between them, some of it inferred.",
  f'<rect x="3.5" y="5" width="7" height="14" rx=".5"/><rect x="13.5" y="5" width="7" height="14" rx=".5"/><path {A} stroke-dasharray="2 2" d="M12 5v14"/>'),
 ("ic-topics","Topics","The commonplace book: where topoi were kept, with its ribbon.",
  f'<rect x="5" y="3.5" width="13.5" height="17" rx="1"/><path d="M8 3.5v17"/><path {A} d="M13.5 3.5v6.8l1.5-1.3 1.5 1.3V3.5"/>'),
 ("ic-genre","Genre","Binder tabs: query, CFP, job, announcement, each filed apart.",
  f'<rect x="4.5" y="6" width="15" height="14.5" rx="1"/><path d="M5.5 6V4h3.5v2M15 6V4h3.5v2M8 11h8M8 14h8M8 17h5"/><path {A} d="M10.3 6V4h3.4v2"/>'),
 ("ic-network","Participation","The seminar table: the circle of conversation and its seats.",
  f'<circle {A} cx="12" cy="12" r="4"/><circle cx="12" cy="4.6" r="1.5"/><circle cx="19.4" cy="12" r="1.5"/><circle cx="12" cy="19.4" r="1.5"/><circle cx="4.6" cy="12" r="1.5"/>'),
 ("ic-flashpoint","Flashpoints","A margin comment with an exclamation: something here got heated.",
  f'<path d="M4.5 5h15v10.5h-8l-4 3.5v-3.5h-3z"/><path {A} d="M12 7.8v3.6"/><circle {AF} cx="12" cy="13.5" r=".9"/>'),
 ("ic-citation","Citations","The open book with its bookmark: a work the list talked about.",
  f'<path d="M12 7c-2-1.5-5-2-8-1.5v12.5c3-.5 6 0 8 1.5 2-1.5 5-2 8-1.5V5.5c-3-.5-6 0-8 1.5zM12 7v12.5"/><path {A} d="M15 5.4v5.3l1.3-1 1.3 1V5.2"/>'),
 ("ic-institution","Institutions","Old Main: the pediment and columns every campus seems to have.",
  f'<path {A} d="M3.5 9.5L12 4.5l8.5 5z"/><path d="M6 11.5v6M10 11.5v6M14 11.5v6M18 11.5v6M3.5 19.5h17"/>'),
 ("ic-event","Events","The conference lectern and its gooseneck microphone.",
  f'<path d="M5 10.5l1.5-4h11l1.5 4zM12 10.5v8.5M8 20h8"/><path {A} d="M15 6.5c0-1.5.6-2.5 2-3"/>'),
 ("ic-optout","Opt out","A letter returned to its sender.",
  f'<rect x="3.5" y="8.5" width="13" height="10" rx="1"/><path d="M3.5 9.5l6.5 4.5 6.5-4.5"/><path {A} d="M21 5.5h-6M17 3.5l-2 2 2 2"/>'),
 ("ic-about","About","The syllabus: a heading, then the policies.",
  f'<rect x="5" y="3.5" width="14" height="17" rx="1"/><path {A} d="M8 7.5h8"/><path d="M8 11h8M8 14h8M8 17h5"/>'),
 ("ic-quoted","Quoted text","The quotation rule in the margin of a reply.",
  f'<path {A} d="M6.5 5v14"/><path d="M10 7h9.5M10 11h9.5M10 15h6"/>'),
 ("ic-attachment","Attachment","The paperclip: something was sent with this, and is not kept here.",
  f'<path {A} d="M15.5 8.5l-6 6a2 2 0 0 0 2.8 2.8l7-7a3.5 3.5 0 0 0-5-5l-7 7a5 5 0 0 0 7 7l5.2-5.2"/>'),
 ("ic-job","Job posting","A flyer pinned to the department corkboard.",
  f'<rect x="6" y="5.5" width="12" height="15" rx=".5"/><path d="M9 11h6M9 14h6M9 17h3"/><circle {AF} cx="12" cy="5.5" r="1.7"/>'),
 ("ic-cfp","Call for papers","The abstract page, its corner turned to mark the deadline.",
  f'<path d="M4 6a1 1 0 0 1 1-1h11l4 4v10a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1z"/><path d="M7 10h6M7 13h10M7 16h8"/><path {A} d="M16 5v4h4"/>'),
 ("ic-tribute","Tribute","The pen nib, for a colleague remembered in writing.",
  f'<path d="M12 3.5l4.8 6.2L12 20.5 7.2 9.7z"/><path {A} d="M12 10v6.5"/><circle cx="12" cy="8.6" r=".8"/>'),
 ("ic-annotate","Annotate and code","The pencil, point down, writing in the margin.",
  f'<path d="M15.5 4.5l4 4L9 19l-4.5 1 1-4.5z"/><path d="M13.5 6.5l4 4"/><path {A} d="M5.5 15.5l3.5 3.5"/>'),
 ("ic-method","Methodology","The rhetorical triangle, with the text at its center.",
  f'<path d="M12 4l8.5 15h-17z"/><circle {AF} cx="12" cy="13.5" r="1.6"/>'),
 ("ic-redpen","Feedback and flags","The red-pen check in a margin: reviewed, or flagged for review.",
  f'<rect x="4" y="4" width="16" height="16" rx="1"/><path d="M7.5 8h5M7.5 12h5M7.5 16h5"/><path {A} d="M14.5 12l1.7 1.8 2.8-3.8"/>'),
]

def symbol(i):
    id_, name, note, body = i
    return (f'  {{# {name}. {note} #}}\n'
            f'  <symbol id="{id_}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">\n'
            f'    {body}\n  </symbol>\n')

header = '''{# ============================================================
   WPA-L icon sprite. Same grammar as the Pinakes Alexandrian sprite,
   different vocabulary.

   Drawing rules (inherited from Pinakes so the two sets read as one hand):
     · 24x24 grid, content inset to 3-21, nothing on the bleed
     · fill none, stroke currentColor, 1.6 width, round caps and joins
     · exactly ONE accented element per icon, carrying the meaning
     · accents via inline style + --ic-accent, because document CSS
       does not cross the shadow tree a <use> clone lives in

   Vocabulary: the composition classroom, the writing program office,
   and the listserv's own apparatus -- the quoted-reply chevron, the
   envelope, the signature line -- where Pinakes used the Alexandrian
   critical marks. Nothing invented where a familiar object means it.
   ============================================================ #}
<svg class="ic-sprite" width="0" height="0" aria-hidden="true" focusable="false"><defs>

'''
(ROOT / 'templates' / '_icons_wpa.html').write_text(
    header + '\n'.join(symbol(i) for i in ICONS) + '\n</defs></svg>\n', encoding='utf-8')

def csp_hash(attr):
    value = attr.split('"')[1]
    return "'sha256-" + base64.b64encode(hashlib.sha256(value.encode()).digest()).decode() + "'"

index = ['"""Generated by tools/icons/build_icons.py. Do not edit."""', '',
         'ICONS = [']
index += [f'    ({id_!r}, {name!r}, {note!r}),' for id_, name, note, _ in ICONS]
index += [']', '', f'STYLE_HASHES = [{csp_hash(A)!r}, {csp_hash(AF)!r}]', '']
(ROOT / 'wpal' / 'icons.py').write_text('\n'.join(index), encoding='utf-8')

# preview sheet (resolved colours, since cairosvg has no CSS vars)
def sheet(bg, ink, acc, sub, out):
    cols, cw, ch = 7, 150, 150
    rows = -(-len(ICONS)//cols)
    W, H = cols*cw, rows*ch
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}"><rect width="100%" height="100%" fill="{bg}"/>']
    for n,(id_,name,note,body) in enumerate(ICONS):
        x, y = (n%cols)*cw, (n//cols)*ch
        b = body.replace('var(--ic-accent)', acc)
        parts.append(f'<g transform="translate({x+39},{y+18}) scale(3)" fill="none" stroke="{ink}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">{b}</g>')
        parts.append(f'<text x="{x+cw/2}" y="{y+118}" text-anchor="middle" font-family="sans-serif" font-size="12" fill="{ink}">{name}</text>')
        parts.append(f'<text x="{x+cw/2}" y="{y+134}" text-anchor="middle" font-family="monospace" font-size="10" fill="{sub}">{id_}</text>')
        # small size check
        parts.append(f'<g transform="translate({x+122},{y+8})" fill="none" stroke="{ink}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">{b}</g>')
    parts.append('</svg>')
    cairosvg.svg2png(bytestring=''.join(parts).encode(), write_to=out)

if cairosvg:
    sheet('#F5EFE1','#1C1A17','#A8462C','#6A5F4E', str(HERE / 'preview-red-figure.png'))
    sheet('#171512','#F0E9DA','#DD7C5F','#ADA18C', str(HERE / 'preview-black-figure.png'))
else:
    print('cairosvg not installed: previews not regenerated')
print(len(ICONS), 'icons')
