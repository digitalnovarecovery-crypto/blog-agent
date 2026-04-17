"""Demo run 2: Virtual IOP + Sober Living in Austin blog post."""
import sys, json, time, requests
sys.stdout.reconfigure(encoding="utf-8")
from requests.auth import HTTPBasicAuth
from server import (
    _wp_upload_featured_image, _save_post, _count_words_html,
    _validate_yoast, AUTHOR_ID, CTA_HTML, MIN_WORD_COUNT,
)

s = requests.Session()
s.auth = HTTPBasicAuth("shehan", "DS7g pM5l D4vF cl4I TOQg Eixx")
s.headers.update({"User-Agent": "EudaimoniaBlogAgent/1.0"})
base = "https://eudaimoniahomes.com/wp-json/wp/v2"

title = "How Virtual IOP Works With Sober Living in Austin, TX"
slug = "virtual-iop-sober-living-austin-tx"
focus_keyphrase = "virtual IOP sober living"
seo_title = "Virtual IOP Sober Living in Austin TX Guide"
meta_description = (
    "Learn how virtual IOP sober living programs in Austin TX work. "
    "Balance recovery, work, and structured living with evening telehealth sessions."
)
excerpt = (
    "Virtual IOP lets you attend intensive outpatient therapy from your sober living home in Austin. "
    "Evening telehealth sessions fit around work schedules, making it easier to balance recovery, "
    "employment, and daily structure without the commute."
)

content_html = (
    '<h2>Balancing Recovery and Work: Why Virtual IOP Is a Game-Changer for Sober Living Residents</h2>'
    '<p>One of the biggest concerns people have when entering sober living is how to balance their recovery program with holding down a job. If you are living in a sober home in Austin and need intensive outpatient treatment, the idea of driving across town three nights a week can feel overwhelming, especially when Austin traffic is unpredictable. That is exactly why <a href="https://eudaimoniahomes.com/sober-living/austin-tx/men/structured-mens-sober-living-austin/">virtual IOP programs paired with sober living in Austin</a> have become one of the most practical paths to sustained recovery.</p>'
    '<p>Virtual Intensive Outpatient Programs allow you to attend therapy sessions from the comfort of your sober living home using a phone, tablet, or laptop. You get the same clinical support, group therapy, and individual counseling that in-person IOP provides, but without the commute, the gas money, or the stress of rushing from work to a treatment center across the city.</p>'

    '<h2>What Is Virtual IOP and How Does It Work?</h2>'
    '<p>Intensive Outpatient Programs are structured treatment programs designed for people who need more support than weekly therapy but do not require residential or inpatient care. A standard IOP typically involves three sessions per week, each lasting about three hours. Virtual IOP delivers the same clinical programming through secure video conferencing platforms.</p>'
    '<p>For sober living residents in Austin, virtual IOP sessions are usually scheduled in the evening, often from 6:00 p.m. to 9:00 p.m. on Monday, Wednesday, and Thursday evenings. This schedule is intentionally designed to work around standard employment hours, so you can go to work during the day, come home to your sober living house, and log into your session from a quiet space in the home.</p>'
    '<p>The program typically runs for eight weeks and includes group therapy, psychoeducation, relapse prevention skills training, and individual check-ins with a licensed counselor. Most programs accept major insurance plans, which significantly reduces out-of-pocket costs for participants.</p>'

    '<h2>Why Virtual IOP Pairs Perfectly With Sober Living</h2>'
    '<p>Sober living homes already provide the structured, substance-free environment that supports recovery. Adding virtual IOP to that foundation creates a comprehensive recovery experience without the logistical headaches. Here is why the combination works so well:</p>'
    '<ul>'
    '<li><strong>No commute required.</strong> Austin traffic can turn a twenty-minute drive into an hour-long ordeal. Virtual IOP eliminates that variable entirely. You finish work, come home, have dinner with your housemates, and log into your session from your room or a common area.</li>'
    '<li><strong>Consistent environment.</strong> Attending therapy from the same stable, sober environment every session reduces distractions and helps you stay focused on your recovery work.</li>'
    '<li><strong>Built-in accountability.</strong> Your housemates and house manager know your IOP schedule. They can help make sure the house is quiet during your sessions and check in with you afterward about how things went.</li>'
    '<li><strong>Lower cost.</strong> Without transportation expenses and with insurance coverage, virtual IOP is one of the most affordable ways to get intensive clinical support while in sober living.</li>'
    '<li><strong>Flexibility for shift workers.</strong> If your work schedule does not follow a traditional nine-to-five pattern, many virtual IOP providers offer alternative session times or allow you to make up missed sessions.</li>'
    '</ul>'

    '<h2>What Happens if You Miss a Session?</h2>'
    '<p>Life happens, and occasionally you may need to miss a virtual IOP session. Most programs require 24-hour advance notice if you cannot attend. You simply email or call your counselor ahead of time, and the missed session gets added to the end of your program so you still complete all required hours.</p>'
    '<p>If you miss a session without giving advance notice, there may be a small fee, typically around fifty dollars. This policy exists to encourage consistency, which is one of the most important factors in successful recovery. The good news is that with virtual sessions, there are far fewer reasons to miss. You can log in from anywhere with an internet connection, whether you are at home, on a break at work, or even in your car during your commute home.</p>'

    '<h2>How Sober Living in Austin Supports Your IOP Journey</h2>'
    '<p>At <a href="https://eudaimoniahomes.com/sober-living/austin-tx/">Eudaimonia Recovery Homes in Austin</a>, residents have access to reliable Wi-Fi, quiet spaces for private sessions, and a community that understands the importance of showing up for treatment. The structured schedule of sober living, which includes set meal times, house meetings, and curfews, actually complements the IOP schedule rather than competing with it.</p>'
    '<p>Many residents find that the combination of <a href="https://eudaimoniahomes.com/sober-living-austin-tx-structured-recovery/">structured sober living</a> and virtual IOP creates a rhythm to their days that reduces anxiety and builds the kind of routine that carries over into long-term sobriety. Morning routines, work during the day, evening therapy, and peer support at night create a full circle of recovery that is hard to replicate on your own.</p>'
    '<p>House managers at Eudaimonia are also familiar with the IOP process and can help coordinate schedules so that house meetings and chore rotations do not conflict with your therapy sessions. This kind of practical support makes a real difference when you are trying to juggle recovery, work, and daily responsibilities.</p>'

    '<h2>The Austin Advantage: Recovery Resources at Your Doorstep</h2>'
    '<p>Austin has one of the most active recovery communities in Texas. In addition to virtual IOP, sober living residents have access to dozens of weekly <a href="https://eudaimoniahomes.com/aa-meeting-finder-troubleshooting-austin-tx/">AA and NA meetings across the city</a>, outdoor activities like hiking the Greenbelt and kayaking on Lady Bird Lake, and a growing network of sober social events.</p>'
    '<p>The city also has a strong job market, which means residents who are balancing work and recovery have real career opportunities available to them. From the tech industry to hospitality to healthcare, Austin provides options for people at every stage of their professional restart. And because virtual IOP does not require you to be near a specific treatment center, you can choose a sober living home based on what is closest to your workplace rather than your therapy provider.</p>'

    '<h2>Is Virtual IOP Right for You?</h2>'
    '<p>Virtual IOP is a strong fit for people who meet any of the following criteria:</p>'
    '<ul>'
    '<li>You have completed a detox or residential treatment program and need step-down care.</li>'
    '<li>You are living in a sober home and want to add clinical support to your recovery plan.</li>'
    '<li>You are working full-time or part-time and need evening therapy options.</li>'
    '<li>You prefer the convenience and privacy of attending sessions from home.</li>'
    '<li>You have transportation challenges or live in an area of Austin where driving to a treatment center is impractical.</li>'
    '</ul>'
    '<p>If you are unsure whether virtual IOP is the right level of care, the admissions team at <a href="https://eudaimoniahomes.com/sober-living/austin-tx/">Eudaimonia Recovery Homes</a> can help you evaluate your options and connect you with trusted IOP providers who work with sober living residents.</p>'

    '<h2>Getting Started With Virtual IOP in Austin Sober Living</h2>'
    '<p>Starting virtual IOP while in sober living is straightforward. The first step is to contact your sober living home or an IOP provider to discuss your treatment needs. If you have insurance, the provider will verify your coverage and walk you through what to expect. Most residents are able to begin their IOP program within a few days of enrollment.</p>'
    '<p>From there, you simply show up to your sessions on time, participate actively, and lean on your sober living community for support between sessions. The combination of clinical treatment and peer accountability is one of the most effective approaches to early recovery, and virtual IOP makes it more accessible than ever before.</p>'

    '<h2>Take the Next Step in Your Recovery</h2>'
    '<p>If you are ready to combine the stability of sober living with the clinical support of virtual IOP, the team at <a href="https://eudaimoniahomes.com/sober-living/austin-tx/">Eudaimonia Recovery Homes in Austin</a> is here to help you build a recovery plan that works with your life, not against it.</p>'
)

# ── Validations ──────────────────────────────────────────────────────────
wc = _count_words_html(content_html)
print(f"Word count: {wc} (min: {MIN_WORD_COUNT})")
assert wc >= MIN_WORD_COUNT, f"FAIL: {wc} words"

yoast_err = _validate_yoast(focus_keyphrase, seo_title, meta_description)
if yoast_err:
    print(f"YOAST FAIL: {yoast_err}")
    sys.exit(1)
print(f"SEO title: {len(seo_title)} chars (30-60 range)")
print(f"Meta desc: {len(meta_description)} chars (120-156 range)")
print(f"Focus keyphrase: {focus_keyphrase}")
print(f"Excerpt: {excerpt[:60]}...")

# ── Inject 2 CTAs (Austin post) ─────────────────────────────────────────
paragraphs = content_html.split("</p>")
insert_point = len(paragraphs) // 3
paragraphs.insert(insert_point, f"</p>{CTA_HTML}")
content_html = "</p>".join(paragraphs) + CTA_HTML
print("CTAs injected: 2")

# ── Featured image ───────────────────────────────────────────────────────
print("Uploading featured image...")
media_id = None
try:
    img_r = requests.get("https://api.unsplash.com/search/photos", params={
        "query": "person laptop peaceful home evening",
        "per_page": 3,
        "content_filter": "high",
    }, headers={"Authorization": "Client-ID 2i9eqWHBrhRbm7rB-J_m86bSL4Mg_-NL8yzPeq1NBTc"})
    photo = img_r.json()["results"][0]
    img_url = photo["urls"]["regular"]
    photographer = photo.get("user", {}).get("name", "Unsplash")
    img_data = requests.get(img_url).content
    print(f"Downloaded: {len(img_data)//1024}KB by {photographer}")

    upload = s.post(f"{base}/media",
        headers={
            "Content-Disposition": 'attachment; filename="virtual-iop-sober-living-austin.jpg"',
            "Content-Type": "image/jpeg",
        },
        data=img_data)
    if upload.ok:
        media_id = upload.json()["id"]
        s.post(f"{base}/media/{media_id}", json={
            "alt_text": f"Person attending virtual therapy session at home - Photo by {photographer} on Unsplash"
        })
        print(f"Featured image: media ID {media_id}")
except Exception as e:
    print(f"Image error: {e}")

# ── Categories + Tags ────────────────────────────────────────────────────
cat_ids = []
for name in ["Sober Living", "Austin", "Recovery Resources"]:
    time.sleep(0.5)
    try:
        r = s.get(f"{base}/categories", params={"search": name})
        if r.ok and r.text.strip() and r.json():
            cat_ids.append(r.json()[0]["id"])
    except: pass

tag_ids = []
for name in ["sober-living-austin", "virtual-iop", "intensive-outpatient-austin"]:
    time.sleep(0.5)
    try:
        r = s.get(f"{base}/tags", params={"search": name})
        if r.ok and r.text.strip() and r.json():
            tag_ids.append(r.json()[0]["id"])
    except: pass

# ── Publish ──────────────────────────────────────────────────────────────
payload = {
    "title": title,
    "content": content_html,
    "slug": slug,
    "categories": cat_ids,
    "tags": tag_ids,
    "status": "future",
    "date": "2026-03-28T14:00:00",
    "author": AUTHOR_ID,
    "excerpt": excerpt,
    "meta": {
        "_yoast_wpseo_focuskw": focus_keyphrase,
        "_yoast_wpseo_title": seo_title,
        "_yoast_wpseo_metadesc": meta_description,
    },
}
if media_id:
    payload["featured_media"] = media_id

resp = s.post(f"{base}/posts", json=payload)
resp.raise_for_status()
post = resp.json()
wp_id = post.get("id", 0)
_save_post(wp_id, title, slug, "2026-03-28T14:00:00-05:00")

# Verify Yoast fields were saved
verify = s.get(f"{base}/posts/{wp_id}?context=edit").json()
v_meta = verify.get("meta", {})

print("\n" + json.dumps({
    "success": True,
    "wp_post_id": wp_id,
    "title": title,
    "url": post.get("link", ""),
    "scheduled_for": "2026-03-28 2:00 PM CT",
    "author": "Basil Ciocon",
    "word_count": wc,
    "featured_image": f"set (media #{media_id})" if media_id else "failed",
    "ctas_injected": 2,
    "internal_links": 6,
    "yoast": {
        "focus_keyphrase": v_meta.get("_yoast_wpseo_focuskw", "NOT SET"),
        "seo_title": f'{v_meta.get("_yoast_wpseo_title", "NOT SET")} ({len(v_meta.get("_yoast_wpseo_title", ""))} chars)',
        "meta_description": f'{v_meta.get("_yoast_wpseo_metadesc", "NOT SET")} ({len(v_meta.get("_yoast_wpseo_metadesc", ""))} chars)',
    },
    "excerpt": excerpt[:80] + "...",
}, indent=2))
