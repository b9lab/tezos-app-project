from datetime import datetime, timedelta
import smartpy as sp

# PEQ contract sample 
# see https://github.com/C-ORG/whitepaper for the definitions

# Contract needs an organization(administrator), a minimal funding goal(MFG) in mutez
# and a minimum period of time(MPT) in years for initialization

class PEQ(sp.Contract):
    def __init__(
                self, 
                organization, 
                initial_price, 
                MFG, 
                MPT, 
                b, 
                s, 
                preminted, 
                I, 
                D, 
                minimumInvestment =sp.tez(1),
                burned_tokens = 0,
                company_valuation,
                base_currency = "tez",
                total_allocation,
                stake_allocation,
                termination_events,
                govRights,
                company_name
                ):

        self.init(
            organization  = organization,                                     # contract administrator
            ledger        = sp.map(l = {organization: sp.as_nat(preminted)}), # token ledger
            price         = initial_price,                                    # initial price before MFG
            total_tokens  = preminted,
            burned_tokens = burned_tokens,
            MFG           = MFG,                                              # minimal funding goal
            MPT           = sp.timestamp(
                                        int(
                                            (datetime.now() + timedelta(days = 365 * MPT)).timestamp()
                                        )
                                    ), # minimum period of time
            I             = I,          # percentage of the funds being held in the cash reserve
            D             = D,          # percentage of the revenues being funneled into cash reserve
            b             = b,          # buy slope
            s             = s,          # sell slope
            minimumInvestment  = minimumInvestment,
            company_v          = company_valuation,
            base_currency      = base_currency,
            total_allocation   = total_allocation,
            stake_allocation   = stake_allocation, 
            termination_events = termination_events,
            govRights          = govRights,
            company_name       = company_name,
            phase              = 0,                                              # starting under MFG
            total_investment   = sp.tez(0)
            )

    # square root for buy and sell calculus
    @sp.global_lambda
    def square_root(x):
        sp.verify(x >= 0)
        y = sp.local('y', x)
        sp.while y.value * y.value > x:
            y.value = (x // y.value + y.value) // 2
        sp.verify((y.value * y.value <= x) & (x < (y.value + 1) * (y.value + 1)))
        sp.result(y.value)

    # s calculus after each transaction
    def modify_sell_slope(self, send_back= sp.tez(0)):
        sp.if self.data.total_tokens != 0:
           self.data.s = 2 * sp.utils.mutez_to_nat(sp.balance - send_back) / (self.data.total_tokens * self.data.total_tokens)
    # initial phase, the price is fix
    def buy_initial(self, amount):
        # calculate amount of tokens from sp.amount and the price
        token_amount = sp.local(
            "token_amount", 
            sp.ediv(
                amount, 
                self.data.price
                ).open_some("Fatal Error: Price is zero")
            )

        # fail if no tokens can be issued with this amount of tez
        sp.if sp.fst(token_amount.value) == sp.as_nat(0):
            sp.failwith("No token can be issued, please send more tez")
            
        # check if the address owns tokens
        sp.if self.data.ledger.contains(sp.sender):
            # add amount of the tokens into the ledger
            self.data.ledger[sp.sender] += sp.fst(token_amount.value)
        sp.else:
            # put amount of the tokens into the ledger
            self.data.ledger[sp.sender] = sp.fst(token_amount.value)
            
        # increase total amount of the tokens
        self.data.total_tokens += sp.fst(token_amount.value)

        # keep received funds in this contract as buyback reserve
        # but send back the excess
        sp.if sp.utils.mutez_to_nat(sp.snd(token_amount.value)) > 0:
            sp.send(sp.sender, sp.snd(token_amount.value))

        # track how much is invested
        self.data.total_investment = self.data.total_investment + amount - sp.snd(token_amount.value)

    # after initial phase, the price will increase
    def buy_slope(self, amount):
        # calculate amount of tokens from amount of tez
        # see https://github.com/C-ORG/whitepaper#buy-calculus

        token_amount = sp.local(
            "token_amount", 
            self.square_root(
                2 * sp.utils.mutez_to_nat(amount) /self.data.b + 
                self.data.total_tokens * self.data.total_tokens
                ) - self.data.total_tokens
            )

        tez_amount = sp.local(
            "tez_amount",
            sp.as_nat(token_amount.value) * self.data.total_tokens * self.data.b   /2 + 
            (sp.as_nat(token_amount.value) + self.data.total_tokens) * sp.as_nat(token_amount.value) * self.data.b/2
            )

        send_back = sp.local(
            "send_back",
            amount - sp.utils.nat_to_mutez(tez_amount.value)
            )

        # send tez that is too much
        sp.if sp.utils.mutez_to_nat(send_back.value) > 0:
            sp.send(sp.sender, send_back.value)

        # track how much is invested
        self.data.total_investment += sp.utils.nat_to_mutez(tez_amount.value)

        # fail if no tokens can be issued with this amount of tez
        sp.if sp.as_nat(token_amount.value) == sp.as_nat(0):
            sp.failwith("No token can be issued, please send more tez")
            
        # calculate buyback reserve from amount I*amount/100
        buyback_reserve = sp.local(
            "buyback_reserve", 
            sp.utils.nat_to_mutez(self.data.I * tez_amount.value / sp.as_nat(100))
            )
        
        company_pay = sp.local(
            "company_pay",
            amount - buyback_reserve.value
            )

        # send (100-I) * amount/100 of the received tez to the organization
        sp.send(self.data.organization, company_pay.value)
        # this will keep I * amount/100 in this contract as buyback reserve
            
        # check if the address owns tokens
        sp.if self.data.ledger.contains(sp.sender):
            self.data.ledger[sp.sender] += sp.as_nat(token_amount.value)
        sp.else:
            self.data.ledger[sp.sender] = sp.as_nat(token_amount.value)
                 
        # increase total amount of the tokens
        self.data.total_tokens += sp.as_nat(token_amount.value)

        # set new price
        self.data.price = sp.utils.nat_to_mutez(self.data.b * self.data.total_tokens)
        self.modify_sell_slope(send_back.value + company_pay.value)

    # buy some tokens with sender's tez
    @sp.entry_point
    def buy(self):
    #check the phase, dont sell or buy if closed
        sp.if self.data.phase != 2:
            # if token in intialization phase, the price is fixed and all funds are escrowed
            sp.if self.data.total_investment < self.data.MFG:
                # check the excess above MFG and send back
                sp.if self.data.MFG - sp.amount < self.data.total_investment:
                    sp.send(sp.sender, sp.amount - (self.data.MFG - self.data.total_investment))
                    self.buy_initial(self.data.MFG - self.data.total_investment)
                sp.else:
                    self.buy_initial(sp.amount)
            # if initialization phase is past
            sp.else:
                self.data.phase = 1
                self.buy_slope(sp.amount)
           
    # internal burn function will be called by the entry points burn and sell
    def burn_intern(self, amount):
        burn_amount= sp.as_nat(amount)
        # check if the address owns tokens
        sp.if self.data.ledger.contains(sp.sender):
            # check if the address owns enough tokens
            sp.if self.data.ledger[sp.sender] >= burn_amount:
                # "burn"
                self.data.ledger[sp.sender] = sp.as_nat(self.data.ledger[sp.sender] - burn_amount)
                self.data.burned_tokens += burn_amount

    @sp.entry_point
    def burn(self, params):
        self.burn_intern(params.amount)
        self.modify_sell_slope()

    def sell_initial(self, amount):
        # check if the address owns tokens
        sp.if self.data.ledger.contains(sp.sender):
        # check if the address owns enough tokens
            sp.if self.data.ledger[sp.sender] >= sp.as_nat(amount):
                # calculate the amount of tez to send
                # see https://github.com/C-ORG/whitepaper#-investments---sell
                pay_amount = sp.local(
                    "pay_amount", 
                    sp.utils.mutez_to_nat(self.data.price) * sp.as_nat(amount)
                )
                # burn the amount of tokens selled
                self.burn_intern(amount)
                # send pay_amount tez to the sender of the transaction
                sp.send(sp.sender, sp.utils.nat_to_mutez(pay_amount.value))
                self.modify_sell_slope(sp.utils.nat_to_mutez(pay_amount.value))

    def sell_slope(self, amount):
        # check if the address owns tokens
        sp.if self.data.ledger.contains(sp.sender):
        # check if the address owns enough tokens
            sp.if self.data.ledger[sp.sender] >= sp.as_nat(amount):
                # calculate the amount of tez to send
                # see https://github.com/C-ORG/whitepaper#-investments---sell
                pay_amount = sp.local(
                    "pay_amount", 
                    sp.as_nat(
                        self.data.total_tokens * sp.as_nat(amount) * self.data.s - 
                        sp.as_nat(amount * amount) * self.data.s / 2 
                        ) + 
                        self.data.s * sp.as_nat(amount) * 
                        self.data.burned_tokens * self.data.burned_tokens /
                        sp.as_nat(2 * (self.data.total_tokens - self.data.burned_tokens) )
                )
                # burn the amount of tokens selled
                self.burn_intern(amount)
                # send pay_amount tez to the sender of the transaction
                sp.send(sp.sender, sp.utils.nat_to_mutez(pay_amount.value))
                self.modify_sell_slope(sp.utils.nat_to_mutez(pay_amount.value))

    @sp.entry_point
    def sell(self, params):
        sp.if self.data.phase == 0:
            self.sell_initial(params.amount)
        sp.if self.data.phase == 1:
            self.sell_slope(params.amount)

    @sp.entry_point
    def close(self):
        #check MPT is over
        sp.verify(sp.now - self.data.MPT >= 0)

        # check that the initial phase is over but not closed
        sp.verify(self.data.phase == 1)

        # verify this is called by the org
        sp.verify(sp.sender == self.data.organization)

        # check the correct amount of tez is sent
        closing_sell_price= sp.local(
            "closing_sell_price",
            self.data.b * self.data.total_tokens
            )

        closing_sell_amount= sp.local(
            "closing_sell_amount",
            closing_sell_price.value * sp.as_nat(self.data.total_tokens - self.data.burned_tokens)
            )

        sp.if sp.balance < sp.utils.nat_to_mutez(closing_sell_amount.value):
            sp.failwith("Please send more tez for the closing")

        sp.for account in self.data.ledger.items():
            sp.send(account.key, sp.utils.nat_to_mutez(account.value * closing_sell_price.value))
        
        self.data.phase = 2

    @sp.entry_point
    def pay(self):
        # check that the initial phase is over but not closed
        sp.verify(self.data.phase == 1)
        # see https://github.com/C-ORG/whitepaper#-revenues---pay
        buyback_reserve = sp.local(
            "local_amount", 
            sp.utils.nat_to_mutez(
                sp.utils.mutez_to_nat(sp.amount) * self.data.D / 100
                )
            )
        # send sp.amount - buyback_reserve to organization
        d = sp.amount - buyback_reserve.value
        sp.send(self.data.organization, d)

        # create the same amount of tokens a buy call would do
        token_amount = sp.local(
        "token_amount", 
        self.square_root(
            2 * sp.utils.mutez_to_nat(d) /self.data.b + 
            self.data.total_tokens * self.data.total_tokens
            ) - self.data.total_tokens
        )

        # give those tokes to the organisation
        self.data.ledger[self.data.organization] = sp.as_nat(token_amount.value)
                
        # increase total amount of the tokens
        self.data.total_tokens += sp.as_nat(token_amount.value)

@sp.add_test(name= "Initialization")
def initialization():
    
    # dummy addresses
    organization = sp.address("tz1hRTppkUow3wQNcj9nZ9s5snwc6sGC8QHh")
    buyer1 = sp.address("tz1xbuyer1")
    buyer2 = sp.address("tz1xbuyer2")

    contract= PEQ(
        organization = organization, 
        b = 2000, 
        s = 1000, 
        initial_price = sp.tez(1), 
        MFG = sp.tez(1000), 
        preminted = 0,
        MPT = 1, # minimal period of time in years
        I = 90,
        D = 80, 
        company_valuation = 1000000,
        total_allocation = 4000,
        stake_allocation = 500,
        termination_events = ["Sale", "Bankruptcy"],
        govRights = "None",
        company_name = "TZMINT Demo"
        )
    
    scenario = sp.test_scenario()
    scenario += contract
    
    # buy some tokens till reaching MFG check the price is fix
    scenario += contract.buy().run(sender = buyer1, amount = sp.mutez(500001000))
    
    # try to sell some before phase 1
    scenario += contract.sell(amount=1).run(sender = buyer1)

    scenario += contract.buy().run(sender = buyer2, amount = sp.mutez(200003000))
    scenario += contract.buy().run(sender = buyer1, amount = sp.tez(299))
    scenario.verify(contract.data.price == sp.tez(1))

    # now MFG is reached, buy more and see price increasing
    scenario += contract.buy().run(sender = buyer1, amount = sp.tez(50))
    scenario += contract.buy().run(sender = buyer2, amount = sp.tez(400))
    scenario += contract.buy().run(sender = buyer1, amount = sp.tez(100))
    scenario.verify(contract.data.price > sp.tez(1))
    
    scenario += contract.buy().run(sender = buyer1, amount = sp.mutez(51245389))
    scenario += contract.buy().run(sender = buyer1, amount = sp.tez(100))
    
    # now sell some tokens
    scenario += contract.sell(amount=100).run(sender = buyer1)

    # and see how the price changes if you buy again
    scenario += contract.buy().run(sender = buyer1, amount = sp.tez(150))
    scenario += contract.buy().run(sender = buyer1, amount = sp.tez(150))
    scenario += contract.buy().run(sender = buyer1, amount = sp.tez(150))
    scenario += contract.buy().run(sender = buyer1, amount = sp.tez(50))

    # Check price for selling 1 token
    scenario += contract.sell(amount=1).run(sender = buyer1)

    # Check closing before MPT
    scenario += contract.close().run(sender = organization, valid=False, amount = sp.tez(2400), now= sp.timestamp_from_utc_now().add_days(360))
    # Check closing with wrong account
    scenario += contract.close().run(sender = buyer1, valid=False, now= sp.timestamp_from_utc_now().add_days(365))
    # Check closing with too less tez
    scenario += contract.close().run(sender = organization, valid=False, amount = sp.tez(300), now= sp.timestamp_from_utc_now().add_days(365))
    # Check closing with correct amount of tez
    scenario += contract.close().run(sender = organization, amount = sp.tez(2400), now= sp.timestamp_from_utc_now().add_days(365))