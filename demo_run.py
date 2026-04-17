"""Demo workflow: write and publish a 1000+ word blog post with all rules."""
import sys, json, time, requests
sys.stdout.reconfigure(encoding="utf-8")
from requests.auth import HTTPBasicAuth
from server import _wp_upload_featured_image, _save_post, _count_words_html, AUTHOR_ID, CTA_HTML

s = requests.Session()
s.auth = HTTPBasicAuth("shehan", "DS7g pM5l D4vF cl4I TOQg Eixx")
s.headers.update({"User-Agent": "EudaimoniaBlogAgent/1.0"})
base = "https://eudaimoniahomes.com/wp-json/wp/v2"

title = "What Happens on Your First Day at a Sober Living Home in Austin, TX"
slug = "first-day-sober-living-home-austin-tx"

content_html = (
    '<h2>Moving Into Sober Living: What Your First Day Actually Looks Like</h2>'
    '<p>The decision to move into a sober living home is one of the most important steps you will take in your recovery journey. But once you have made that decision, a new wave of questions hits: What do I bring? What happens when I arrive? Will I feel welcome? If you are considering <a href="https://eudaimoniahomes.com/sober-living/austin-tx/men/structured-mens-sober-living-austin/">sober living in Austin, TX</a>, here is an honest look at what your first day will be like so you can walk through the door feeling prepared and confident.</p>'

    '<h2>Before You Arrive: Completing the Intake Process</h2>'
    '<p>Your first day actually starts before you set foot in the house. Most sober living homes in Austin, including <a href="https://eudaimoniahomes.com/sober-living/austin-tx/">Eudaimonia Recovery Homes</a>, have an intake process that handles the paperwork ahead of time. This typically includes signing a program agreement, providing emergency contact information, and arranging your initial payment.</p>'
    '<p>During this step, the admissions team will answer your questions about house rules, expectations, and what personal items you should bring. They will also coordinate with the house manager to make sure your bed is reserved and the room is ready for your arrival. Many residents find that completing these steps ahead of time makes the actual move-in day feel much less stressful.</p>'
    '<p>If a family member is helping with payment or co-signing the agreement, the admissions team will reach out to them directly to collect signatures and payment information. This is a common arrangement and nothing to feel uncomfortable about. The goal is to remove every barrier between you and your fresh start.</p>'

    '<h2>Arriving at the House: Your First Impressions</h2>'
    '<p>When you pull up to the sober living house, the first thing most residents notice is how normal everything looks. These are real homes in real Austin neighborhoods, not clinical facilities with fluorescent lights. At Eudaimonia, the Austin houses are located in welcoming residential areas with easy access to public transportation, grocery stores, and local recovery meetings.</p>'
    '<p>The house manager will greet you at the door. Think of this person as your primary point of contact for the first few weeks. They will show you around the house, introduce you to any residents who are home, and walk you through the common areas including the kitchen, living room, laundry facilities, and outdoor spaces. You will be shown your bedroom and given time to settle in and unpack.</p>'
    '<p>Most residents are surprised by how comfortable the homes feel. Bedrooms are furnished with a bed, dresser, and closet space. Common areas are clean and maintained by the residents as part of the shared responsibility structure. If you are moving into a <a href="https://eudaimoniahomes.com/sober-living/austin-tx/pet-friendly-mens-sober-living-austin/structured-pet-friendly-mens-sober-living-austin/">pet-friendly sober living home in Austin</a>, the house manager will also walk you through the pet guidelines and designated areas.</p>'

    '<h2>The Welcome Orientation: Understanding House Rules</h2>'
    '<p>After you have had time to settle into your room, the house manager will sit down with you for a welcome orientation. This is not a lecture or an interrogation. It is a straightforward conversation about how the house operates so that everyone can live together successfully. Topics covered during orientation typically include:</p>'
    '<ul>'
    '<li><strong>Curfew times</strong> and quiet hours so everyone gets adequate rest</li>'
    '<li><strong>Drug and alcohol testing schedules</strong> to maintain a safe and sober environment</li>'
    '<li><strong>Chore rotations</strong> so the house stays clean and responsibilities are shared equally</li>'
    '<li><strong>Guest policies</strong> including when visitors are allowed and where they can go</li>'
    '<li><strong>House meeting schedules</strong> which are typically held weekly</li>'
    '<li><strong>Emergency contacts</strong> and what to do if you need immediate support</li>'
    '<li><strong>Expectations around employment or program participation</strong> as residents are encouraged to stay active and productive during their time in the house</li>'
    '</ul>'
    '<p>The rules exist to protect everyone in the house, including you. Knowing them from day one means you will never be caught off guard. If anything feels unclear, the house manager is there to answer every question. There are no bad questions on your first day.</p>'

    '<h2>Meeting Your Housemates: Building Your Support System</h2>'
    '<p>One of the most meaningful parts of your first day is meeting the people you will be living with. Your housemates are not strangers in the traditional sense. They are people who understand exactly what you are going through because they are walking the same path. Many of them felt the same nervousness on their first day that you might be feeling right now.</p>'
    '<p>Do not be surprised if someone offers to show you around the neighborhood, invites you to a recovery meeting that evening, or simply sits down to talk over coffee. The community aspect of sober living is one of its most powerful features. Research consistently shows that social support is one of the strongest predictors of long-term recovery success.</p>'
    '<p>At <a href="https://eudaimoniahomes.com/sober-living/mens-sober-living-in-south-austin-tx/structured-mens-sober-living-south-austin-tx/">Eudaimonia South Austin</a>, the homes are intentionally kept small enough that every resident knows each other by name. This creates the kind of genuine accountability that helps people stay on track during the critical early months of recovery.</p>'

    '<h2>Your First Evening: Settling Into the Routine</h2>'
    '<p>By the time evening rolls around on your first day, most of the logistical pieces are in place. You have unpacked, you know the house rules, you have met some of your housemates, and the initial anxiety has started to fade. Many houses encourage new residents to attend a recovery meeting on their first night, whether that is an <a href="https://eudaimoniahomes.com/aa-meeting-finder-troubleshooting-austin-tx/">AA meeting in Austin</a>, an NA meeting, or another program that fits your recovery plan.</p>'
    '<p>Dinner is often a communal affair, with residents taking turns cooking or preparing meals together. This is one of those small but significant details that makes sober living feel like a home rather than a program. Sharing a meal with people who genuinely want to see you succeed can be a powerful reminder of why you made this decision in the first place.</p>'
    '<p>Before bed, take a few minutes to set up your space the way you want it. Hang up your clothes, charge your phone, set an alarm for the morning. These small acts of creating order in your personal space reinforce the sense of stability that structured sober living provides.</p>'

    '<h2>What to Bring on Move-In Day</h2>'
    '<p>Packing for sober living is simpler than most people expect. Here is a practical checklist of what to bring on your first day:</p>'
    '<ul>'
    '<li>A valid photo ID and any important personal documents</li>'
    '<li>A week or two of clothing appropriate for the season</li>'
    '<li>Basic toiletries including shampoo, toothbrush, and any prescribed medications</li>'
    '<li>Bedding such as sheets and a pillow if you prefer your own, though most homes provide basics</li>'
    '<li>A phone and charger for staying connected with family and your recovery network</li>'
    '<li>Any recovery literature, journals, or personal items that support your sobriety</li>'
    '</ul>'
    '<p>Leave valuables at home or with a trusted family member. Sober living homes are safe environments, but keeping things simple helps you focus on what matters most: your recovery.</p>'

    '<h2>Why Austin Is a Great Place to Start Fresh</h2>'
    '<p>Austin offers unique advantages for people entering sober living. The city has a thriving recovery community, warm weather that encourages outdoor activities year-round, and a job market that provides real opportunities for people rebuilding their careers. The combination of <a href="https://eudaimoniahomes.com/sober-living-austin-tx-structured-recovery/">structured sober living</a> with everything Austin has to offer creates an environment where recovery feels less like sacrifice and more like a genuine fresh start.</p>'
    '<p>From hiking the Greenbelt to attending one of the dozens of weekly recovery meetings across the city, Austin gives you the tools and the community to build a life you are proud of. And it all starts with that first day walking through the door of your sober living home.</p>'

    '<h2>Ready to Take the First Step?</h2>'
    '<p>If you or a loved one is considering sober living in Austin, the team at <a href="https://eudaimoniahomes.com/sober-living/austin-tx/">Eudaimonia Recovery Homes</a> is ready to walk you through the intake process and answer every question you have. Your first day does not have to be scary. With the right preparation and the right home, it can be the beginning of everything you have been working toward.</p>'
)

# ── Rule 5: Verify 1000+ words ──────────────────────────────────────────
wc = _count_words_html(content_html)
print(f"Word count: {wc}")
assert wc >= 1000, f"FAIL: only {wc} words"

# ── Rule 3: Inject 2 CTAs (Austin post) ─────────────────────────────────
paragraphs = content_html.split("</p>")
insert_point = len(paragraphs) // 3
paragraphs.insert(insert_point, f"</p>{CTA_HTML}")
content_html = "</p>".join(paragraphs) + CTA_HTML
print("CTAs injected: 2")

# ── Rule 1: Featured image from Unsplash ────────────────────────────────
print("Uploading featured image...")
media_id = None
try:
    img_r = requests.get("https://api.unsplash.com/search/photos", params={
        "query": "cozy residential house porch morning",
        "per_page": 3,
        "content_filter": "high",
    }, headers={"Authorization": "Client-ID 2i9eqWHBrhRbm7rB-J_m86bSL4Mg_-NL8yzPeq1NBTc"})
    photo = img_r.json()["results"][0]
    img_url = photo["urls"]["regular"]
    photographer = photo.get("user", {}).get("name", "Unsplash")
    img_data = requests.get(img_url).content
    print(f"Downloaded image: {len(img_data)//1024}KB by {photographer}")

    upload = s.post(
        f"{base}/media",
        headers={
            "Content-Disposition": 'attachment; filename="first-day-sober-living-austin.jpg"',
            "Content-Type": "image/jpeg",
        },
        data=img_data,
    )
    if upload.ok:
        media_id = upload.json()["id"]
        s.post(f"{base}/media/{media_id}", json={
            "alt_text": f"Welcoming residential home porch - Photo by {photographer} on Unsplash"
        })
        print(f"Featured image uploaded: media ID {media_id}")
    else:
        print(f"Upload failed: {upload.status_code}")
except Exception as e:
    print(f"Image error: {e}")

# ── Resolve categories ──────────────────────────────────────────────────
cat_ids = []
for name in ["Sober Living", "Austin", "Recovery Resources"]:
    time.sleep(0.5)
    try:
        r = s.get(f"{base}/categories", params={"search": name})
        if r.ok and r.text.strip() and r.json():
            cat_ids.append(r.json()[0]["id"])
    except:
        pass
print(f"Categories resolved: {cat_ids}")

# ── Resolve tags ────────────────────────────────────────────────────────
tag_ids = []
for name in ["sober-living-austin", "first-day-sober-living", "move-in-sober-living"]:
    time.sleep(0.5)
    try:
        r = s.get(f"{base}/tags", params={"search": name})
        if r.ok and r.text.strip() and r.json():
            tag_ids.append(r.json()[0]["id"])
    except:
        pass

# ── Rule 4: Author = Basil Ciocon | Publish ─────────────────────────────
payload = {
    "title": title,
    "content": content_html,
    "slug": slug,
    "categories": cat_ids,
    "tags": tag_ids,
    "status": "future",
    "date": "2026-03-28T09:00:00",
    "author": AUTHOR_ID,
}
if media_id:
    payload["featured_media"] = media_id

resp = s.post(f"{base}/posts", json=payload)
resp.raise_for_status()
post = resp.json()
wp_id = post.get("id", 0)
_save_post(wp_id, title, slug, "2026-03-28T09:00:00-05:00")

print("\n" + json.dumps({
    "success": True,
    "wp_post_id": wp_id,
    "title": title,
    "url": post.get("link", ""),
    "scheduled_for": "2026-03-28 9:00 AM CT",
    "author": "Basil Ciocon",
    "word_count": wc,
    "featured_image": f"set (media #{media_id})" if media_id else "failed",
    "ctas_injected": 2,
    "internal_links": 7,
}, indent=2))
