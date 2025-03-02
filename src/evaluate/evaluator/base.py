# Copyright 2022 The HuggingFace Datasets Authors and the TensorFlow Datasets Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional, Union

# Lint as: python3
from datasets import Dataset, load_dataset


try:
    from scipy.stats import bootstrap

    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False

try:
    from transformers import (
        FeatureExtractionMixin,
        Pipeline,
        PreTrainedModel,
        PreTrainedTokenizer,
        PreTrainedTokenizerBase,
        TFPreTrainedModel,
        pipeline,
    )

    TRANSFORMERS_AVAILABLE = True
except ImportError:
    TRANSFORMERS_AVAILABLE = False

from typing_extensions import Literal

from ..loading import load
from ..module import EvaluationModule
from ..utils.logging import get_logger


logger = get_logger(__name__)


class Evaluator(ABC):
    """
    The Evaluator class is the class from which all evaluators inherit. Refer to this class for methods shared across
    different evaluators.
    Base class implementing evaluator operations.
    """

    def __init__(self, task: str, default_metric_name: str = None):
        if not TRANSFORMERS_AVAILABLE:
            raise ImportError(
                "If you want to use the `Evaluator` you need `transformers`. Run `pip install evaluate[evaluator]`."
            )
        if not SCIPY_AVAILABLE:
            raise ImportError(
                "If you want to use the `Evaluator` you need `scipy>=1.7.1`. Run `pip install evaluate[evaluator]`."
            )
        self.task = task
        self.default_metric_name = default_metric_name

    @staticmethod
    def _compute_confidence_interval(
        predictions,
        references,
        metric: EvaluationModule,
        metric_keys: List[str],
        confidence_level: float = 0.95,
        n_resamples: int = 9999,
        random_state: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        A utility function enabling the confidence interval calculation for metrics computed
        by the evaluator based on `scipy`'s `bootstrap` method.
        """
        bootstrap_dict = {}
        for key in metric_keys:
            bs = bootstrap(
                data=(predictions, references),
                statistic=lambda predictions, references: metric.compute(
                    predictions=predictions,
                    references=references,
                )[key],
                paired=True,
                vectorized=False,
                confidence_level=confidence_level,
                n_resamples=n_resamples,
                random_state=random_state,
            )
            bootstrap_dict[key] = {
                "confidence_interval": (bs.confidence_interval.low, bs.confidence_interval.high),
                "standard_error": bs.standard_error,
            }
        return bootstrap_dict

    @abstractmethod
    def compute(
        self,
        model_or_pipeline: Union[str, "Pipeline", Callable, "PreTrainedModel", "TFPreTrainedModel"] = None,
        data: Union[str, Dataset] = None,
        metric: Union[str, EvaluationModule] = None,
        tokenizer: Optional[Union[str, "PreTrainedTokenizer"]] = None,
        strategy: Literal["simple", "bootstrap"] = "simple",
        confidence_level: float = 0.95,
        n_resamples: int = 9999,
        **compute_parameters: Dict,
    ):
        """
        A core method of the `Evaluator` class, computes the metric value for a pipeline and dataset compatible
        with the task specified by the `Evaluator`.
        """
        raise NotImplementedError()

    def prepare_data(self, data: Union[str, Dataset], input_column: str, label_column: str):
        """
        Prepare data.

        Args:
            data (`str` or `Dataset`, defaults to `None):
                Specifies the dataset we will run evaluation on. If it is of type `str`, we treat it as the dataset
                name, and load it. Otherwise we assume it represents a pre-loaded dataset.
            input_column (`str`, defaults to `"text"`):
                the name of the column containing the text feature in the dataset specified by `data`.
            label_column (`str`, defaults to `"label"`):
                the name of the column containing the labels in the dataset specified by `data`.
        Return:
            Loaded `datasets.Dataset`.
        """
        if data is None:
            raise ValueError(
                "Please specify a valid `data` object - either a `str` with a name or a `Dataset` object."
            )
        data = load_dataset(data) if isinstance(data, str) else data
        if input_column not in data.column_names:
            raise ValueError(
                f"Invalid `input_column` {input_column} specified. The dataset contains the following columns: {data.column_names}."
            )
        if label_column not in data.column_names:
            raise ValueError(
                f"Invalid `label_column` {label_column} specified. The dataset contains the following columns: {data.column_names}."
            )

        return data

    def prepare_pipeline(
        self,
        model_or_pipeline: Union[str, "Pipeline", Callable, "PreTrainedModel", "TFPreTrainedModel"],
        preprocessor: Union["PreTrainedTokenizerBase", "FeatureExtractionMixin"] = None,
    ):
        """
        Prepare pipeline.

        Args:
            model_or_pipeline (`str` or `Pipeline` or `Callable` or `PreTrainedModel` or `TFPreTrainedModel`,
            defaults to `None`):
                If the argument in not specified, we initialize the default pipeline for the task (in this case
                `text-classification` or its alias - `sentiment-analysis`). If the argument is of the type `str` or
                is a model instance, we use it to initialize a new `Pipeline` with the given model. Otherwise we assume the
                argument specifies a pre-initialized pipeline.
            preprocessor (`PreTrainedTokenizerBase` or `FeatureExtractionMixin`, *optional*, defaults to `None`):
                Argument can be used to overwrite a default preprocessor if `model_or_pipeline` represents a model for
                which we build a pipeline. If `model_or_pipeline` is `None` or a pre-initialized pipeline, we ignore
                this argument.
        Returns:
            The initialized pipeline.
        """
        if (
            isinstance(model_or_pipeline, PreTrainedModel)
            or isinstance(model_or_pipeline, TFPreTrainedModel)
            or isinstance(model_or_pipeline, str)
        ):
            pipe = pipeline(self.task, model=model_or_pipeline, tokenizer=preprocessor, feature_extractor=preprocessor)
        else:
            if model_or_pipeline is None:
                pipe = pipeline(self.task)
            else:
                pipe = model_or_pipeline
            if preprocessor is not None:
                logger.warning("Ignoring the value of the preprocessor argument (`tokenizer` or `feature_extractor`).")
        if pipe.task != self.task:
            raise ValueError(
                f"Incompatible `model_or_pipeline`. Please specify `model_or_pipeline` compatible with the `{self.task}` task."
            )
        return pipe

    def prepare_metric(self, metric: Union[str, EvaluationModule]):
        """
        Prepare metric.

        Args:
            metric (`str` or `EvaluationModule`, defaults to `None`):
                Specifies the metric we use in evaluator. If it is of type `str`, we treat it as the metric name, and
                load it. Otherwise we assume it represents a pre-loaded metric.

        Returns:
            The loaded metric.
        """
        # Prepare metric.
        if metric is None:
            if self.default_metric_name is None:
                raise ValueError(
                    "`Evaluator` doesn't specify a default metric. Please specify a valid `metric` argument."
                )
            metric = load(self.default_metric_name)
        elif isinstance(metric, str):
            metric = load(metric)

        return metric

    def core_compute(
        self,
        references: List,
        predictions: List,
        metric: EvaluationModule,
        strategy: Literal["simple", "bootstrap"] = "simple",
        confidence_level: float = 0.95,
        n_resamples: int = 9999,
        random_state: Optional[int] = None,
    ):
        """Compute and return metrics."""
        result = metric.compute(predictions=predictions, references=references)

        if strategy == "bootstrap":
            metric_keys = result.keys()
            bootstrap_dict = Evaluator._compute_confidence_interval(
                predictions,
                references,
                metric,
                metric_keys,
                confidence_level,
                n_resamples,
                random_state,
            )
            for key in metric_keys:
                bootstrap_dict[key]["score"] = result[key]

            return bootstrap_dict

        return result
