# University article readers

On 30 September 2026, UCL Ventures, Sheffield and Edinburgh returned usable news listings,
but full articles were withheld because the article reader used its generic
WordPress-style selectors for these sites. That removed useful university
discovery evidence before company extraction.

The readers now use the article body containers inspected on these official
pages:

- [UCL Ventures](https://www.uclventures.com/spinout-portfolio-news/2026/seed-funding-will-bring-cascaders-pioneering-ai-eye-care-closer-reality):
  the basic-content columns inside the main article's left-content area.
- [University of Sheffield](https://www.sheffield.ac.uk/news/next-wave-display-technology-set-be-unlocked-university-sheffield-spinout):
  the news-article body block inside main content.
- [Edinburgh Innovations](https://edinburgh-innovations.ed.ac.uk/news/student-startup-solarsub-raises-1-3m-for-solar-panel-technology):
  the four article prose blocks within the reviewed article container. This
  page has no main element; the selector uses its named contentBlock container to
  exclude the navigation and footer. Article-owned related links are retained.

Only those named containers supply article text and labelled company links.
Navigation, related-news sidebars and footers remain excluded. Unrecognised
layouts still withhold the article. Existing robots/noindex checks, public-host
checks, timeouts, redirect limits and the article budget stay in force.

This restores article collection; it does not assert that every university
company qualifies for Today or has never received funding. Existing identity,
country, maturity, backing, scoring and final-review checks still apply.

Validation: all three inspected live articles now yield substantive body text and
article links. Offline regressions cover exclusion of navigation/sidebar links,
unrecognised layouts and footer-only lookalikes, alongside the existing article
hydration and source safety tests. Captured main-content DOM fixtures preserve
the actual nesting and classes; their text and links are replaced with fictional
data. These fixtures separately verify body-only text and link extraction.
Edinburgh also has a regression that removes styling classes from the captured
page: the named article container still yields the same body-owned links.
