"""Public HTML news indexes; API noindex is not a reason to bypass policy."""
import re
from urllib.parse import urlsplit, urljoin
from radar.sources._common import html_doc, first_text, parse_date, guard_nonempty, selector_fingerprint


def public_posts(payload, key, base, selectors):
    doc=html_doc(payload,key)
    cards=doc.css(selectors)
    posts=[]
    for card in cards:
        link=card.css_first('.post-title a, h2 a, h3 a, h4 a, h5 a')
        if not link:
            continue
        url=urljoin(base,link.attributes.get('href',''))
        if urlsplit(url).hostname!=urlsplit(base).hostname:
            continue
        title=link.text(strip=True)
        if not title:
            continue
        time=card.css_first('time')
        stated=(time.attributes.get('datetime') if time else None) or first_text(card,('.post-date',))
        stamp=parse_date(stated)
        if stamp is None:
            # UKTN embeds an exact publication date in the final slug token.
            match=re.search(r'-(\d{4})(\d{2})(\d{2})/?$',urlsplit(url).path)
            stamp=parse_date('-'.join(match.groups())) if match else None
        posts.append(dict(id=url,link=url,title=title,date=stamp,
                          body='',excerpt=first_text(card,('.post-excerpt','.entry-summary')) or '',
                          full_text_in_feed=False))
    guard_nonempty(key,posts,detail='no public news article cards',document=payload)
    # Article IDs and topic classes change with every publication; fingerprint
    # the mandatory selector path rather than content-dependent CSS classes.
    return posts,selector_fingerprint([selectors,'heading>a[href]'])
