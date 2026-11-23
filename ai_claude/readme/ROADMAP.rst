* Claude only: the chain asks no other provider; an assistant with a model of
  its own asks it itself.
* Prices are a table in the code: a model missing from it costs 0, and a
  price change is a code change.
* Odoo keeps no budget: the Anthropic Console workspace's spend limit is the
  only cap.
* A breaker write that meets a concurrent one is dropped: one count is lost,
  which the next question makes up.
