-- A funding amount stated in another currency, as stated ("USD 4000000"),
-- kept beside the converted total_funding_gbp so a card can show both.
ALTER TABLE company ADD COLUMN funding_original TEXT;
