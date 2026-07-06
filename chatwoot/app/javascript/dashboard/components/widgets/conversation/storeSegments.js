// Durian — COCO vs FOFO classification for the Google Reviews showrooms.
//
// COCO = Company-Owned Company-Operated, FOFO = Franchise-Owned
// Franchise-Operated. Keyed by the `store-*` label slug the reviews poller
// creates from each Google Business Profile listing title
// (reviews_poller._store_label: lowercase, non-alphanumeric → '-'), so the
// keys match the labels already on review conversations.
//
// Source: the client's COCO/FOFO showroom sheet (30 COCO + 25 FOFO),
// reconciled against the live Google listings. Stores not yet reviewed are
// pre-listed so the tag appears the moment their first review lands. Stores
// absent here (doors / FHC / project centres, and any not on the client
// sheet) simply show no tag. To reclassify a store, edit its entry here.
export const STORE_SEGMENTS = {
  'store-durian-furniture-agra-sikandra': 'FOFO',
  'store-durian-furniture-ahmedabad-bodakdev': 'COCO',
  'store-durian-furniture-aizawl-chaltlang': 'FOFO',
  'store-durian-furniture-ajmer-vaishali-nagar': 'FOFO',
  'store-durian-furniture-bengaluru-banaswadi': 'COCO',
  'store-durian-furniture-bengaluru-jp-nagar': 'FOFO',
  'store-durian-furniture-bengaluru-marathahalli': 'COCO',
  'store-durian-furniture-bengaluru-shivaji-nagar': 'FOFO',
  'store-durian-furniture-bhubaneshwar-patia': 'FOFO',
  'store-durian-furniture-bhubaneshwar-samantarapur': 'COCO',
  'store-durian-furniture-bikaner-rani-bazar': 'FOFO',
  'store-durian-furniture-chennai-nungambakkam': 'COCO',
  'store-durian-furniture-chennai-omr-perungudi': 'COCO',
  'store-durian-furniture-chhatrapati-sambhajinagar-aurangabad': 'COCO',
  'store-durian-furniture-coimbatore-rs-puram': 'FOFO',
  'store-durian-furniture-delhi-ghitorni': 'COCO',
  'store-durian-furniture-delhi-gurgaon-jmd': 'COCO',
  'store-durian-furniture-delhi-kirti-nagar': 'COCO',
  'store-durian-furniture-delhi-sohna-road': 'COCO',
  'store-durian-furniture-dhanbad-hirapur': 'FOFO',
  'store-durian-furniture-faridabad-mewla-maharajpur': 'COCO',
  'store-durian-furniture-guwahati-lachitnagar': 'COCO',
  'store-durian-furniture-hyderabad-banjara-hills': 'COCO',
  'store-durian-furniture-hyderabad-kompally': 'COCO',
  'store-durian-furniture-hyderabad-sarath-city': 'COCO',
  'store-durian-furniture-indore-vijay-nagar': 'COCO',
  'store-durian-furniture-jaipur-vaishali-nagar': 'COCO',
  'store-durian-furniture-jammu-kunjwani': 'COCO',
  'store-durian-furniture-jamshedpur-adityapur': 'COCO',
  'store-durian-furniture-kanpur-rai-purwa': 'FOFO',
  'store-durian-furniture-kolkata-topsia': 'COCO',
  'store-durian-furniture-lucknow-arjunganj': 'FOFO',
  'store-durian-furniture-lucknow-indira-nagar': 'FOFO',
  'store-durian-furniture-ludhiana-daad-village': 'COCO',
  'store-durian-furniture-mohali-jlpl-industrial-area': 'FOFO',
  'store-durian-furniture-motihari-bankat': 'FOFO',
  'store-durian-furniture-mumbai-goregaon': 'COCO',
  'store-durian-furniture-mumbai-worli': 'COCO',
  'store-durian-furniture-noida-sector-10': 'COCO',
  'store-durian-furniture-noida-sector-49': 'FOFO',
  'store-durian-furniture-panchkula': 'FOFO',
  'store-durian-furniture-patna-danapur': 'COCO',
  'store-durian-furniture-prayagraj-phaphamau': 'FOFO',
  'store-durian-furniture-pune-baner': 'COCO',
  'store-durian-furniture-pune-creaticity': 'COCO',
  'store-durian-furniture-ranchi-ashok-nagar': 'COCO',
  'store-durian-furniture-sambalpur': 'FOFO',
  'store-durian-furniture-shillong-demseiniong': 'FOFO',
  'store-durian-furniture-siliguri-sevoke-road': 'FOFO',
  'store-durian-furniture-surat': 'COCO',
  'store-durian-furniture-thane-subhash-nagar': 'FOFO',
  'store-durian-furniture-tirupati-avilala': 'FOFO',
  'store-durian-furniture-vadodara-subhanpura': 'FOFO',
  'store-durian-furniture-varanasi-shivpur': 'FOFO',
  'store-durian-furniture-visakhapatnam-dondaparthy': 'FOFO',
};

// 'COCO' | 'FOFO' | '' for a `store-*` label title.
export const storeSegment = labelTitle => STORE_SEGMENTS[labelTitle] || '';
