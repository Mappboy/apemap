DROP VIEW IF EXISTS public.member_secondary_school_education_46, public.member_aph_46;
create view public.member_aph_46
as
SELECT m.id,
       m.member,
       p.party,
       p.party_abbrev,
       m.district,
       m.is_senator,
       m.is_representative,
       m.mp_id,
       m.start,
       m.wiki_link,
       m.dob,
       m.chamber,
       m.high_school,
       ap."GivenName",
       ap."MiddleNames",
       ap."FamilyName",
       ap."PreferredName",
       ap."DisplayName",
       ap."DateOfBirth",
       ap."DateOfDeath",
       ap."PlaceOfBirth",
       ap."PlaceOfDeath",
       ap."SecondarySchool",
       ap."Image",
       ap."Gender",
       ap."MaritalStatus",
       ap."CountryOfBirth",
       ap."StateOfBirth",
       ap."StateOfDeath",
       ap."Electorate",
       ap."SenateState",
       ap."State",
       ap."StateAbbrev",
       ap."StateOrTerritory",
       ap."InCurrentParliament",
       ap."ServiceHistory_Start",
       ap."ServiceHistory_End",
       ap."ServiceHistory_Days",
       ap."ServiceHistory_Duration",
       ap."Age",
       ap."Age_String",
       ap."PortraitNote",
       ap."ElectedMemberNo",
       ap."ElectedSenatorNo",
       ap."FirstNations",
       ap."FirstNationsText",
       ap."MPorSenator",
       ap."RepresentedParliaments",
       ap."RepresentedParties",
       ap."RepresentedElectorates",
       ap."RepresentedStates",
       ap."RepresentedMinistries",
       ap."RepresentedShadowMinistries",
       ap."ParliamentaryPositions",
       ap."Honours",
       ap."Occupations",
       ap."SecondaryOccupations",
       ap."Qualifications",
       ap."ElectorateService",
       ap."PartyParliamentaryService",
       ap."PartyCommitteeService"
FROM members m
    JOIN aec_parties p on  m.party_id = p.id
         JOIN aph_parliamentarians ap ON m.mp_id = ap."PHID"
WHERE 46 = ANY (ap."RepresentedParliaments");

alter table public.member_aph_46
    owner to cam;
