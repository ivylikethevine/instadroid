"""The CSS every plain-HTML page starts from: /status, /browse and the static export."""

PAGE_STYLE: str = """
body { font-family: system-ui, sans-serif; max-width: 720px; margin: 2rem auto; padding: 0 1rem;
       background: #fafafa; color: #222; }
h1 { font-size: 1.3rem; } h2 { font-size: 1rem; }
table { border-collapse: collapse; width: 100%; }
td, th { text-align: left; padding: 0.25rem 0.6rem; border-bottom: 1px solid #ddd; font-size: 0.9rem; }
article { border-bottom: 1px solid #ddd; padding-bottom: 1rem; margin-bottom: 1rem; overflow-wrap: anywhere; }
button { font: inherit; padding: 0.2rem 0.7rem; }
"""
