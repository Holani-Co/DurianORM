json.id offer.id
json.caption offer.caption
json.priority offer.priority
json.active offer.active
json.tags offer.tags
json.link offer.link
json.expires_at offer.expires_at
json.image_url offer.image.attached? ? url_for(offer.image) : nil
json.created_at offer.created_at
