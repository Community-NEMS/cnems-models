 old name         | new name                     | holds                                                                                                                   
------------------|------------------------------|-------------------------------------------------------------------------------------------------------------------------
 region_int_trade | region_domestic_trading      | domestic regions that trade internationally post-region screen using TradeLimitCapInt to identify connections           
 region_int       | region_international_trading | international regions that are trading from "region1" column in TradeLimitCapInt post screening of domestic connections 
region1 (in preprocessor) | ? | all regions domestic and international that pass filtering above


region labelling
- in TranLimit, region1 is destination and is the domestic region set
- in TranLimitCapInt, region1 is the international destination region