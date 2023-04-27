create view public.member_secondary_school_education
as
SELECT row_number() over () as id,
       m.id as member_id,
       m.member,
       m.party,
       m.district,
       m.chamber,
       CASE
           WHEN m.party_abbrev = ANY (ARRAY ['ALP'::text, 'LNP'::text, 'GRN'::text, 'NP'::text, 'IND'::text])
               THEN m.party_abbrev
           ELSE 'Other'::text
           END            AS party_abbrv,
       m.mp_id,
       m.wiki_link        AS "wiki link",
       m.dob,
       m."Image",
       m."Gender",
       m."StateAbbrev",
       m."Occupations",
       m."SecondaryOccupations",
       m."Qualifications",
       e.name,
       e.link,
       e.operational_status,
       e.is_international,
       e.school_sector,
       ea.acara_id,
       al.suburb          AS school_suburb,
       al."school sector" AS al_school_sector,
       al."school type",
       al."statistical area 2 name",
       al."local government area name",
       al."commonwealth electoral division",
       eaf.australian_government_recurrent_funding_per_student,
       eaf.australian_government_recurrent_funding_total,
       eaf.state__territory_government_recurring_funding_per_student,
       eaf.state__territory_government_recurring_funding_total,
       eaf.fees_charges_and_parent_contributions_per_student,
       eaf.fees_charges_and_parent_contributions_total,
       eaf.other_private_sources_per_student,
       eaf.other_private_sources_total,
       eaf.total_gross_income_per_student,
       eaf.total_gross_income_total,
       e.geometry         AS geom
FROM member_aph m
         LEFT JOIN member_education me ON m.id = me.member_id
         JOIN education e ON me.education_id = e.id
         LEFT JOIN education_acara ea ON me.education_id = ea.education_id
         LEFT JOIN acara_school_locations_2022 al ON ea.acara_id = al."acara sml id"
         LEFT JOIN acara_education_finances eaf ON ea.acara_id = eaf.acara_id
WHERE e.is_high_school;

alter table public.member_secondary_school_education
    owner to cam;
