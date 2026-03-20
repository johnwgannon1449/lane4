from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 630

SERIF_BOLD   = '/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf'
SANS_REGULAR = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
SANS_BOLD    = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'

LOGO_SZ  = 160
FYL_SZ   = 56
TAG_SZ   = 48
BADGE_SZ = 22

BG_TOP    = (10, 22, 42)
BG_BOT    = (6, 13, 28)
WHITE     = (255, 255, 255)
WHITE_DIM = (200, 215, 235)
BLUE      = (59, 130, 246)

img  = Image.new('RGB', (W, H), BG_TOP)
draw = ImageDraw.Draw(img)

# Vertical gradient
for y in range(H):
    t = y / H
    r = int(BG_TOP[0] + (BG_BOT[0] - BG_TOP[0]) * t)
    g = int(BG_TOP[1] + (BG_BOT[1] - BG_TOP[1]) * t)
    b = int(BG_TOP[2] + (BG_BOT[2] - BG_TOP[2]) * t)
    draw.line([(0, y), (W, y)], fill=(r, g, b))

# Subtle blue radial glow top-left
cx, cy = 160, 160
for r2 in range(320, 0, -1):
    alpha = max(0, 20 - int(20 * r2 / 320))
    color = (
        min(255, BG_TOP[0] + alpha),
        min(255, BG_TOP[1] + alpha + 5),
        min(255, BG_TOP[2] + alpha + 28),
    )
    draw.ellipse([cx-r2, cy-r2, cx+r2, cy+r2], fill=color)

LEFT   = 90
logo_y = 90

fLogo  = ImageFont.truetype(SERIF_BOLD,   LOGO_SZ)
fFYL   = ImageFont.truetype(SANS_REGULAR, FYL_SZ)
fTag   = ImageFont.truetype(SANS_REGULAR, TAG_SZ)
fBadge = ImageFont.truetype(SANS_BOLD,    BADGE_SZ)

# "Lane4" — "Lane" in white, "4" in blue
lane_bbox = draw.textbbox((0, 0), 'Lane', font=fLogo)
lane_w = lane_bbox[2] - lane_bbox[0]
draw.text((LEFT, logo_y), 'Lane', fill=WHITE, font=fLogo)
draw.text((LEFT + lane_w, logo_y), '4', fill=BLUE, font=fLogo)

# "Find your lane."
fyl_y = logo_y + LOGO_SZ + 8
draw.text((LEFT, fyl_y), 'Find your lane.', fill=WHITE, font=fFYL)

# Blue divider — matches width of "Find your lane."
fyl_bbox = draw.textbbox((0, 0), 'Find your lane.', font=fFYL)
fyl_w    = fyl_bbox[2] - fyl_bbox[0]
line_y   = fyl_y + FYL_SZ + 18
draw.rectangle([LEFT, line_y, LEFT + fyl_w, line_y + 3], fill=BLUE)

# Tagline
tag_y = line_y + 24
draw.text((LEFT, tag_y), 'Swim recruiting clarity and honesty', fill=WHITE_DIM, font=fTag)

# lane4.app badge
badge_text = 'lane4.app'
b_pad_x, b_pad_y = 14, 8
badge_bbox = draw.textbbox((0, 0), badge_text, font=fBadge)
bw = badge_bbox[2] - badge_bbox[0] + b_pad_x * 2
bh = badge_bbox[3] - badge_bbox[1] + b_pad_y * 2
bx, by = LEFT, H - 72
draw.rounded_rectangle([bx, by, bx + bw, by + bh], radius=20, fill=BLUE)
draw.text((bx + b_pad_x, by + b_pad_y), badge_text, fill=WHITE, font=fBadge)

img.save('static/preview.png', 'PNG')
print('Saved static/preview.png')
