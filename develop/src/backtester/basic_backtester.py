import os
import json
import numpy as np
import pandas as pd
from copy import copy
import matplotlib.pyplot as plt
from abc import abstractmethod
from IPython.display import display, display_markdown
from .utils import data_loader, Position
from common_utils import make_dirs
from collections import OrderedDict, defaultdict
import empyrical as emp


CONFIG = {
    "report_prefix": "001",
    "position_side": "long",
    "entry_ratio": 0.1,
    "commission": 0.0015,
    "min_holding_minutes": 1,
    "max_holding_minutes": 10,
    "compound_interest": True,
    "possible_in_debt": False,
    "achieved_with_commission": False,
    "max_n_updated": None,
}


class BasicBacktester:
    def __init__(
        self,
        base_currency,
        dataset_dir,
        exp_dir,
        report_prefix=CONFIG["report_prefix"],
        position_side=CONFIG["position_side"],
        entry_ratio=CONFIG["entry_ratio"],
        commission=CONFIG["commission"],
        min_holding_minutes=CONFIG["min_holding_minutes"],
        max_holding_minutes=CONFIG["max_holding_minutes"],
        compound_interest=CONFIG["compound_interest"],
        possible_in_debt=CONFIG["possible_in_debt"],
        achieved_with_commission=CONFIG["achieved_with_commission"],
        max_n_updated=CONFIG["max_n_updated"],
    ):
        assert position_side in ("long", "short", "longshort")
        self.base_currency = base_currency
        self.report_prefix = report_prefix
        self.position_side = position_side
        self.entry_ratio = entry_ratio
        self.commission = commission
        self.min_holding_minutes = min_holding_minutes
        self.max_holding_minutes = max_holding_minutes
        self.compound_interest = compound_interest
        self.possible_in_debt = possible_in_debt
        self.achieved_with_commission = achieved_with_commission
        self.max_n_updated = max_n_updated

        # Set path to load data
        dataset_params_path = os.path.join(dataset_dir, "params.json")
        bins_path = os.path.join(dataset_dir, "bins.csv")

        self.report_store_dir = os.path.join(exp_dir, "reports/")
        make_dirs([self.report_store_dir])

        # Load data
        self.bins = self.load_bins(bins_path)
        dataset_params = self.load_dataset_params(dataset_params_path)
        self.q_threshold = dataset_params["q_threshold"]
        self.n_bins = dataset_params["n_bins"]
        self.historical_data_dict = self.build_historical_data_dict(
            base_currency=base_currency,
            historical_data_path_dict={
                "pricing": os.path.join(dataset_dir, "test/pricing.csv"),
                "predictions": os.path.join(
                    exp_dir, "generated_output/predictions.csv"
                ),
                "labels": os.path.join(exp_dir, "generated_output/labels.csv"),
                "q_predictions": os.path.join(
                    exp_dir, "generated_output/q_predictions.csv"
                ),
                "q_labels": os.path.join(exp_dir, "generated_output/q_labels.csv"),
            },
        )
        self.tradable_coins = self.historical_data_dict["pricing"].columns
        self.index = self.historical_data_dict["predictions"].index

        self.initialize()

    def load_tradable_coins(self, tradable_coins_path):
        with open("tradable_coins_path", "r") as f:
            tradable_coins = f.read().splitlines()

        return tradable_coins

    def load_bins(self, bins_path):
        return pd.read_csv(bins_path, header=0, index_col=0)

    def load_dataset_params(self, dataset_params_path):
        with open(dataset_params_path, "r") as f:
            dataset_params = json.load(f)

        return dataset_params

    def build_historical_data_dict(
        self,
        base_currency,
        historical_data_path_dict,
    ):
        historical_data_path_dict = copy(historical_data_path_dict)
        historical_pricing = data_loader(
            path=historical_data_path_dict.pop("pricing"), compression="gzip"
        )
        columns = historical_pricing.columns
        columns_with_base_currency = columns[
            columns.str.endswith(base_currency.upper())
        ]

        data_dict = {}
        for data_type, data_path in historical_data_path_dict.items():
            data_dict[data_type] = data_loader(path=data_path)

            # Re-order columns
            data_dict[data_type].columns = columns

            # Filter by base_currency
            data_dict[data_type] = data_dict[data_type][columns_with_base_currency]

        return data_dict

    def initialize(self):
        self.historical_caches = {}
        self.historical_capitals = {}
        self.historical_entry_reasons = defaultdict(list)
        self.historical_exit_reasons = defaultdict(list)
        self.historical_profits = defaultdict(list)
        self.historical_positions = {}

        self.positions = []
        self.cache = 1

    def report(self, value, target, now, append=False):
        if append is True:
            getattr(self, target)[now].append(value)
            return

        assert now not in getattr(self, target)
        getattr(self, target)[now] = value

    def generate_report(self):
        historical_caches = pd.Series(self.historical_caches).rename("cache")
        historical_capitals = pd.Series(self.historical_capitals).rename("capital")
        historical_returns = (
            pd.Series(self.historical_capitals)
            .pct_change(fill_method=None)
            .fillna(0)
            .rename("return")
        )
        historical_entry_reasons = pd.Series(self.historical_entry_reasons).rename(
            "entry_reason"
        )
        historical_exit_reasons = pd.Series(self.historical_exit_reasons).rename(
            "exit_reason"
        )
        historical_profits = pd.Series(self.historical_profits).rename("profit")
        historical_positions = pd.Series(self.historical_positions).rename("position")

        report = pd.concat(
            [
                historical_caches,
                historical_capitals,
                historical_returns,
                historical_entry_reasons,
                historical_exit_reasons,
                historical_profits,
                historical_positions,
            ],
            axis=1,
        ).sort_index()
        report.index = pd.to_datetime(report.index)

        return report

    def store_report(self, report):
        report.to_csv(
            os.path.join(
                self.report_store_dir,
                f"report_{self.report_prefix}_{self.base_currency}.csv",
            )
        )
        params = {
            "base_currency": self.base_currency,
            "position_side": self.position_side,
            "entry_ratio": self.entry_ratio,
            "commission": self.commission,
            "min_holding_minutes": self.min_holding_minutes,
            "max_holding_minutes": self.max_holding_minutes,
            "compound_interest": self.compound_interest,
            "possible_in_debt": self.possible_in_debt,
            "achieved_with_commission": self.achieved_with_commission,
            "max_n_updated": self.max_n_updated,
            "tradable_coins": tuple(self.tradable_coins.tolist()),
            "q_threshold": self.q_threshold,
        }
        with open(
            os.path.join(
                self.report_store_dir,
                f"params_{self.report_prefix}_{self.base_currency}.csv",
            ),
            "w",
        ) as f:
            json.dump(params, f)

        print(f"[+] Report is stored: {self.report_prefix}_{self.base_currency}")

    def display_accuracy(self):
        accuracies = {}

        for column in self.historical_data_dict["predictions"].columns:
            class_accuracy = {}
            for class_num in range(
                self.historical_data_dict["labels"][column].max() + 1
            ):
                class_mask = (
                    self.historical_data_dict["predictions"][column] == class_num
                )
                class_accuracy["class_" + str(class_num)] = (
                    self.historical_data_dict["labels"][column][class_mask] == class_num
                ).mean()

            accuracy = pd.Series(
                {
                    "total": (
                        self.historical_data_dict["predictions"][column]
                        == self.historical_data_dict["labels"][column]
                    ).mean(),
                    **class_accuracy,
                }
            )
            accuracies[column] = accuracy

        accuracies = pd.concat(accuracies).unstack().T
        display_markdown("#### Accuracy of signals", raw=True)
        display(accuracies)

    def display_metrics(self):
        assert len(self.historical_caches) != 0
        assert len(self.historical_capitals) != 0

        historical_returns = (
            pd.Series(self.historical_capitals).pct_change(fill_method=None).fillna(0)
        )

        metrics = OrderedDict()

        metrics["winning_ratio"] = (
            historical_returns[historical_returns != 0] > 0
        ).mean()
        metrics["sharpe_ratio"] = emp.sharpe_ratio(historical_returns)
        metrics["max_drawdown"] = emp.max_drawdown(historical_returns)
        metrics["avg_return"] = historical_returns.mean()
        metrics["total_return"] = historical_returns.add(1).cumprod().sub(1).iloc[-1]

        display_markdown(f"#### Performance metrics: {self.base_currency}", raw=True)
        display(pd.Series(metrics))

    def display_report(self, report):
        display_markdown(f"#### Report: {self.base_currency}", raw=True)
        _, ax = plt.subplots(3, 1, figsize=(12, 9))

        for idx, column in enumerate(["capital", "return", "cache"]):
            report[column].plot(ax=ax[idx])
            ax[idx].set_title(f"historical {column}")

        plt.tight_layout()
        plt.show()

    @abstractmethod
    def run(self):
        pass
